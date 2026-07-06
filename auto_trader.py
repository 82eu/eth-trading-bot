"""
自动交易引擎 - 基于多周期EMA策略
支持多币种同时监控和交易
"""
from loguru import logger
import threading
import time
from datetime import datetime, timedelta, timezone
from ema_strategy import EMAStrategy
from kline_service import get_kline_service
from config import LEVERAGE, SYMBOL, DEFAULT_SYMBOL, SUPPORTED_SYMBOLS, is_valid_swap_symbol
from feishu_bot import get_feishu_bot


try:
    from config import MOCK_MODE
except ImportError:
    MOCK_MODE = False


class AutoTrader:
    """自动交易引擎"""

    def __init__(self, client):
        self.client = client
        self.strategy = EMAStrategy()
        self.feishu = get_feishu_bot()

        self.running = False
        self._thread = None
        self._stop_event = threading.Event()

        self.config = {
            "enabled_tfs": {"ETH-USDT-SWAP": ["5m", "15m"], "BTC-USDT-SWAP": ["5m", "15m"]},
            "total_amount_usdt": {"ETH-USDT-SWAP": 100, "BTC-USDT-SWAP": 100},
            "num_entries": {"ETH-USDT-SWAP": 2, "BTC-USDT-SWAP": 2},
            "tp_points": {"ETH-USDT-SWAP": 50, "BTC-USDT-SWAP": 500},
            "sl_points": {"ETH-USDT-SWAP": 30, "BTC-USDT-SWAP": 300},
            "buffer_width": {"ETH-USDT-SWAP": 10, "BTC-USDT-SWAP": 100},
            "leverage": LEVERAGE,
            "feishu_enabled": True,
            "enabled_symbols": [DEFAULT_SYMBOL],
            "pending_order_mode": False,  # 挂单模式开关
        }

        self.analysis_cache = {}
        self.signal_logs = []
        self.opened_positions = {}
        self.last_check_time = None
        self._buffer_state = {}
        self.buffer_width = 10
        self._pending_orders = {}  # 跟踪已挂的限价单 {symbol: {order_id: info}}
        self._last_pending_update = None  # 上次更新挂单的时间
        
        self.kline_services = {}
        for symbol in SUPPORTED_SYMBOLS:
            self.kline_services[symbol] = get_kline_service(symbol)

    def start(self):
        if self.running:
            return
        self.running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("自动交易已启动")
        self._add_log("自动交易已启动", "info")
        self._notify_feishu("auto_start")

    def stop(self):
        if not self.running:
            return
        self.running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        
        # 取消所有挂单
        enabled_symbols = self.config.get("enabled_symbols", [DEFAULT_SYMBOL])
        for symbol in enabled_symbols:
            try:
                self.client.cancel_all_pending_orders(symbol)
                symbol_name = symbol.split("-")[0]
                self._add_log(f"[{symbol_name}] 已取消所有挂单", "info")
            except Exception as e:
                logger.error(f"取消挂单异常: {e}")
        
        logger.info("自动交易已停止")
        self._add_log("自动交易已停止", "info")
        self._notify_feishu("auto_stop")

    def _notify_feishu(self, event, **kwargs):
        """飞书通知统一入口，受 feishu_enabled 开关控制"""
        if not self.config.get("feishu_enabled", True):
            return
        if not self.feishu.enabled:
            return
        try:
            symbol = kwargs.get("symbol", DEFAULT_SYMBOL)
            symbol_name = symbol.split("-")[0]
            tf = kwargs.get("tf", "")
            
            if event == "auto_start":
                self.feishu.notify_auto_start()
            elif event == "auto_stop":
                self.feishu.notify_auto_stop()
            elif event == "signal":
                self.feishu.notify_signal(
                    f"{symbol_name}[{tf}]",
                    kwargs.get("direction", ""),
                    kwargs.get("entry_index", 0),
                    kwargs.get("num_entries", 0),
                    kwargs.get("price", 0)
                )
            elif event == "trade_success":
                self.feishu.notify_trade(
                    f"{symbol_name}[{tf}]",
                    kwargs.get("direction", ""),
                    kwargs.get("amount", 0),
                    kwargs.get("price", 0),
                    kwargs.get("tp", 0),
                    kwargs.get("sl", 0),
                    success=True,
                    leverage=kwargs.get("leverage", 100)
                )
            elif event == "trade_fail":
                self.feishu.notify_trade(
                    f"{symbol_name}[{tf}]",
                    kwargs.get("direction", ""),
                    kwargs.get("amount", 0),
                    kwargs.get("price", 0),
                    0, 0,
                    success=False,
                    leverage=kwargs.get("leverage", 100)
                )
            elif event == "buffer_reset":
                self.feishu.notify_buffer_reset(
                    f"{symbol_name}[{tf}]",
                    kwargs.get("price", 0)
                )
        except Exception as e:
            logger.debug(f"飞书通知跳过: {e}")

    def update_config(self, new_config):
        for k, v in new_config.items():
            if k in self.config:
                self.config[k] = v
        logger.info(f"配置已更新: {self.config}")

    def get_status(self):
        if not self.analysis_cache:
            try:
                self.refresh_analysis()
            except Exception as e:
                logger.debug(f"刷新分析数据失败: {e}")
        return {
            "running": self.running,
            "config": self.config,
            "analysis": self.analysis_cache,
            "logs": self.signal_logs[-50:],
            "last_check": self.last_check_time.isoformat() if self.last_check_time else None,
            "buffer_state": self._buffer_state,
            "pending_order_mode": self.config.get("pending_order_mode", False),
            "last_pending_update": self._last_pending_update.isoformat() if self._last_pending_update else None,
        }

    def _run(self):
        while not self._stop_event.is_set():
            try:
                pending_mode = self.config.get("pending_order_mode", False)
                if pending_mode:
                    self._update_pending_orders()
                    self._stop_event.wait(300)  # 挂单模式：5分钟更新一次
                else:
                    self._check_once()
                    self._stop_event.wait(30)  # 实时模式：30秒检查一次
            except Exception as e:
                logger.error(f"检查循环异常: {e}")
                self._add_log(f"检查异常: {e}", "error")
                self._stop_event.wait(60)

    def _update_pending_orders(self):
        """挂单模式：每5分钟更新所有挂单"""
        enabled_symbols = self.config.get("enabled_symbols", [DEFAULT_SYMBOL])
        now = datetime.now()
        
        self._add_log(f"[挂单模式] 开始更新挂单 {now.strftime('%H:%M:%S')}", "info")
        
        for symbol in enabled_symbols:
            if not is_valid_swap_symbol(symbol):
                continue
            
            symbol_name = symbol.split("-")[0]
            enabled_tfs = self._get_enabled_tfs(symbol)
            self._add_log(f"[挂单][{symbol_name}] 启用周期: {enabled_tfs}", "info")
            
            ticker = self.client.get_ticker(symbol)
            if not ticker:
                self._add_log(f"[挂单][{symbol_name}] 获取行情失败", "error")
                continue
            current_price = ticker["last"]
            
            analysis_map = self.refresh_analysis(symbol)
            
            # 先取消所有旧挂单
            cancelled = self.client.cancel_all_pending_orders(symbol)
            if cancelled:
                self._add_log(f"[挂单][{symbol_name}] 已取消旧挂单", "info")
            
            num_entries = self._get_num_entries(symbol)
            total_amount = self._get_total_amount(symbol)
            entry_amount = total_amount / num_entries
            leverage = self.config.get("leverage", LEVERAGE)
            
            total_placed = 0
            total_skipped = 0
            
            # 每个启用的周期都挂单
            for tf in enabled_tfs:
                analysis = analysis_map.get(tf)
                if not analysis:
                    self._add_log(f"[挂单][{symbol_name}][{tf}] 无分析数据，跳过", "info")
                    continue
                
                trend = self.strategy.get_trend_direction(analysis_map, tf)
                a = analysis_map[tf]
                self._add_log(f"[挂单][{symbol_name}][{tf}] EMA180={a['ema180']:.2f}, EMA250={a['ema250']:.2f}, 纠缠={a.get('is_entangled', False)}, 交叉={a.get('recent_crosses', 0)}次, 趋势={trend}", "info")
                if not trend or trend == "neutral":
                    self._add_log(f"[挂单][{symbol_name}][{tf}] 趋势不明确，跳过", "info")
                    continue
                
                entries = self.strategy.calc_entry_levels(analysis, trend, num_entries)
                sl_price = self._calc_sl_with_big_tf_check(analysis_map, tf, trend, self._get_sl_points(symbol))
                
                entries_str = ", ".join([f"{e['description']}={e['price']:.2f}" for e in entries])
                self._add_log(f"[挂单][{symbol_name}][{tf}] 入场位: {entries_str}", "info")
                
                side = "buy" if trend == "long" else "sell"
                tf_placed = 0
                tf_skipped = 0
                
                for entry in entries:
                    entry_price = entry["price"]
                    
                    if trend == "long":
                        if entry_price >= current_price:
                            tf_skipped += 1
                            total_skipped += 1
                            self._add_log(f"[挂单][{symbol_name}][{tf}] {entry['description']} 跳过: 多单入场价{entry_price:.2f}高于现价{current_price:.2f}，挂了会立即成交", "info")
                            continue
                    else:
                        if entry_price <= current_price:
                            tf_skipped += 1
                            total_skipped += 1
                            self._add_log(f"[挂单][{symbol_name}][{tf}] {entry['description']} 跳过: 空单入场价{entry_price:.2f}低于现价{current_price:.2f}，挂了会立即成交", "info")
                            continue
                    
                    tp_price = entry_price + self._get_tp_points(symbol) if trend == "long" else entry_price - self._get_tp_points(symbol)
                    
                    order_id = self.client.place_limit_order_with_tpsl(
                        symbol, side, entry_amount, entry_price, trend,
                        stop_loss=sl_price, take_profit=tp_price, leverage=leverage
                    )
                    
                    if order_id:
                        tf_placed += 1
                        total_placed += 1
                        self._add_log(f"[挂单][{symbol_name}][{tf}] {trend} {entry['description']} 挂单成功: {entry_amount}U @ {entry_price:.2f}, TP={tp_price:.2f}, SL={sl_price:.2f}", "success")
                    else:
                        err = getattr(self.client, 'last_error', '')
                        if '51006' in err or 'price limit' in err.lower():
                            tf_skipped += 1
                            total_skipped += 1
                            self._add_log(f"[挂单][{symbol_name}][{tf}] {entry['description']} 价格偏离过大暂不挂({entry_price:.2f} vs 现价{current_price:.2f})", "info")
                        else:
                            self._add_log(f"[挂单][{symbol_name}][{tf}] {entry['description']} 挂单失败: {err}", "error")
                
                if tf_placed > 0 or tf_skipped > 0:
                    self._add_log(f"[挂单][{symbol_name}][{tf}] 完成，挂单{tf_placed}/{num_entries}个，跳过{tf_skipped}个", "info")
            
            self._add_log(f"[挂单][{symbol_name}] 全部周期完成，共挂单{total_placed}个，跳过{total_skipped}个", "info")
        
        self._last_pending_update = now

    def _check_once(self):
        enabled_symbols = self.config.get("enabled_symbols", [DEFAULT_SYMBOL])
        for symbol in enabled_symbols:
            if not is_valid_swap_symbol(symbol):
                continue
            analysis_map = self.refresh_analysis(symbol)
            enabled_tfs = self._get_enabled_tfs(symbol)
            for tf in enabled_tfs:
                if tf not in analysis_map:
                    continue
                self._check_tf_signal(symbol, tf, analysis_map)

    def _get_candles(self, symbol, timeframe):
        try:
            kline_svc = self.kline_services.get(symbol)
            if not kline_svc:
                kline_svc = get_kline_service(symbol)
                self.kline_services[symbol] = kline_svc
            candles, source = kline_svc.fetch_klines(timeframe, 600, symbol)
            return candles
        except Exception as e:
            logger.error(f"获取{symbol} {timeframe}K线失败: {e}")
            return None

    def _get_symbol_config(self, key, symbol, default_val):
        """按币种获取配置值，支持旧版单值和新版dict格式"""
        val = self.config.get(key, default_val)
        if isinstance(val, dict):
            return val.get(symbol, val.get(DEFAULT_SYMBOL, default_val))
        return val

    def _get_tp_points(self, symbol):
        return self._get_symbol_config("tp_points", symbol, 50)

    def _get_sl_points(self, symbol):
        return self._get_symbol_config("sl_points", symbol, 30)

    def _get_buffer_width(self, symbol):
        return self._get_symbol_config("buffer_width", symbol, 10)

    def _get_total_amount(self, symbol):
        return self._get_symbol_config("total_amount_usdt", symbol, 100)

    def _get_num_entries(self, symbol):
        return int(self._get_symbol_config("num_entries", symbol, 2))

    def _get_enabled_tfs(self, symbol):
        """按币种获取启用的周期列表，兼容旧版全局列表格式"""
        val = self.config.get("enabled_tfs", ["5m", "15m"])
        if isinstance(val, dict):
            return val.get(symbol, val.get(DEFAULT_SYMBOL, ["5m", "15m"]))
        return val

    def refresh_analysis(self, symbol=None):
        if symbol is None:
            symbol = self.config.get("enabled_symbols", [DEFAULT_SYMBOL])[0]
        analysis_map = {}
        for tf in self.strategy.TIMEFRAMES:
            candles = self._get_candles(symbol, tf)
            if candles:
                analysis = self.strategy.analyze_tf(candles, tf)
                if analysis:
                    analysis_map[tf] = analysis
        self.analysis_cache[symbol] = analysis_map
        self.last_check_time = datetime.now()
        return analysis_map

    def _calc_sl_with_big_tf_check(self, analysis_map, tf, direction, sl_points, current_price=None):
        """计算止损价，按用户设置的点数执行，不再自动缩减"""
        small_analysis = analysis_map.get(tf)
        if not small_analysis:
            if current_price:
                return current_price - sl_points if direction == "long" else current_price + sl_points
            # 无分析数据也无当前价格，无法计算
            logger.warning(f"[{tf}] 无分析数据且无当前价格，止损计算失败")
            return None

        sl_price = self.strategy.calc_stop_loss(small_analysis, direction, sl_points)
        return sl_price

    def _check_tf_signal(self, symbol, tf, analysis_map):
        analysis = analysis_map.get(tf)
        if not analysis:
            return

        current_price = analysis["current_price"]
        ema_high = analysis["ema_high"]
        ema_low = analysis["ema_low"]

        buffer_key = f"{symbol}_{tf}"
        buf = self._buffer_state.get(buffer_key)
        if buf is not None:
            if current_price > buf["buffer_high"] or current_price < buf["buffer_low"]:
                self._buffer_state[buffer_key] = None
                logger.info(f"[{symbol}][{tf}] 价格走出缓冲带，重置预警状态")
                self._add_log(f"[{symbol}][{tf}] 价格走出缓冲带，重置预警", "info")
                self._notify_feishu("buffer_reset", symbol=symbol, tf=tf, price=current_price)
            else:
                return

        trend = self.strategy.get_trend_direction(analysis_map, tf)
        if not trend or trend == "neutral":
            return

        num_entries = self._get_num_entries(symbol)
        total_amount = self._get_total_amount(symbol)
        open_amount = total_amount / num_entries

        entries = self.strategy.calc_entry_levels(analysis, trend, num_entries)

        entry_index = -1
        for i, entry in enumerate(entries):
            price = entry["price"]
            if trend == "long":
                if current_price <= price:
                    entry_index = i
            else:
                if current_price >= price:
                    entry_index = i

        if entry_index < 0:
            return

        sl_price = self._calc_sl_with_big_tf_check(analysis_map, tf, trend, self._get_sl_points(symbol))
        tp_price = self.strategy.calc_take_profit(analysis, trend, self._get_tp_points(symbol))

        symbol_name = symbol.split("-")[0]
        self._add_log(f"[{symbol_name}][{tf}] {trend}信号, 入场{entry_index+1}/{num_entries}, 价格{current_price:.2f}", "signal")
        self._notify_feishu("signal", symbol=symbol, tf=tf, direction=trend, entry_index=entry_index+1, num_entries=num_entries, price=current_price)

        side = "buy" if trend == "long" else "sell"
        order_id = self.client.place_order_usdt(
            symbol, side, open_amount,
            pos_side=trend,
            stop_loss=sl_price,
            take_profit=tp_price,
            leverage=self.config["leverage"],
        )

        if order_id:
            position_key = f"{symbol}_{tf}_{trend}_{entry_index}_{int(time.time())}"
            self.opened_positions[position_key] = {
                "symbol": symbol,
                "tf": tf,
                "direction": trend,
                "entry_index": entry_index,
                "price": current_price,
                "time": datetime.now().isoformat(),
                "order_id": order_id,
            }
            buf_w = self._get_buffer_width(symbol)
            self._buffer_state[buffer_key] = {
                "buffer_low": round(ema_low - buf_w, 2),
                "buffer_high": round(ema_high + buf_w, 2),
            }
            self._add_log(f"[{symbol_name}][{tf}] 开{trend}成功 {open_amount}U @ {current_price:.2f}, 缓冲带[{round(ema_low - buf_w, 2)}, {round(ema_high + buf_w, 2)}]", "success")
            self._notify_feishu("trade_success", symbol=symbol, tf=tf, direction=trend, amount=open_amount, price=current_price, tp=tp_price, sl=sl_price, leverage=self.config["leverage"])
        else:
            err = getattr(self.client, 'last_error', '') or '未知错误'
            logger.error(f"[{symbol_name}][{tf}] 开{trend}失败: {err}")
            self._add_log(f"[{symbol_name}][{tf}] 开单失败: {err}", "error")
            self._notify_feishu("trade_fail", symbol=symbol, tf=tf, direction=trend, amount=open_amount, price=current_price, leverage=self.config["leverage"], error=err)

    def test_open_order(self, timeframe, direction=None, symbol=None):
        if symbol is None:
            symbol = self.config.get("enabled_symbols", [DEFAULT_SYMBOL])[0]
        candles = self._get_candles(symbol, timeframe)
        if not candles:
            msg = f"获取{symbol} {timeframe}K线失败"
            logger.error(msg)
            self._add_log(f"[测试][{symbol.split('-')[0]}][{timeframe}] {msg}", "error")
            return False, msg

        analysis = self.strategy.analyze_tf(candles, timeframe)
        if not analysis:
            msg = "策略分析失败"
            logger.error(msg)
            self._add_log(f"[测试][{symbol.split('-')[0]}][{timeframe}] {msg}", "error")
            return False, msg

        if not analysis.get("in_zone", False):
            msg = f"价格不在EMA区间内 (当前价: {analysis['current_price']:.2f}, EMA区间: [{analysis['ema_low']:.2f}, {analysis['ema_high']:.2f}])"
            logger.error(msg)
            self._add_log(f"[测试][{symbol.split('-')[0]}][{timeframe}] {msg}", "error")
            return False, msg

        analysis_map = self.refresh_analysis(symbol) or {timeframe: analysis}

        if direction is None:
            direction = self.strategy.get_trend_direction(analysis_map, timeframe)
            if not direction or direction == "neutral":
                msg = "无法判断趋势"
                self._add_log(f"[测试][{symbol.split('-')[0]}][{timeframe}] {msg}", "error")
                return False, msg

        total_amount = self._get_total_amount(symbol)
        open_amount = total_amount

        current_price = analysis["current_price"]
        sl_price = self._calc_sl_with_big_tf_check(analysis_map, timeframe, direction, self._get_sl_points(symbol), current_price)
        tp_price = self.strategy.calc_take_profit(analysis, direction, self._get_tp_points(symbol))
        leverage = self.config.get("leverage", 10)
        symbol_name = symbol.split("-")[0]
        logger.info(f"[测试][{symbol_name}][{timeframe}] 开{direction}, 金额{open_amount}U, 杠杆{leverage}x, 价格{current_price:.2f}, 止损{sl_price:.2f}, 止盈{tp_price:.2f}")
        self._add_log(f"[测试][{symbol_name}][{timeframe}] 开{direction}, 金额{open_amount}U, 价格{current_price:.2f}, 止损{sl_price:.2f}, 止盈{tp_price:.2f}", "signal")

        side = "buy" if direction == "long" else "sell"
        try:
            order_id = self.client.place_order_usdt(
                symbol, side, open_amount,
                pos_side=direction,
                stop_loss=sl_price,
                take_profit=tp_price,
                leverage=leverage,
            )
        except Exception as e:
            msg = f"下单异常: {e}"
            logger.error(f"[测试][{symbol_name}][{timeframe}] {msg}")
            self._add_log(f"[测试][{symbol_name}][{timeframe}] {msg}", "error")
            return False, msg

        if order_id:
            self._add_log(f"[测试][{symbol_name}][{timeframe}] 开{direction}成功 {open_amount}U", "success")
            return True, {
                "symbol": symbol,
                "direction": direction,
                "amount": open_amount,
                "price": current_price,
                "stop_loss": sl_price,
                "take_profit": tp_price,
                "order_id": order_id,
            }
        else:
            err = getattr(self.client, 'last_error', '')
            balance = self.client.get_balance()
            avail_eq = 0
            if balance:
                for item in balance:
                    for d in item.get("details", []):
                        if d.get("ccy") == "USDT":
                            avail_eq = float(d.get("availEq", "0"))
                            break
            margin_needed = open_amount / leverage
            if avail_eq > 0 and avail_eq < margin_needed:
                msg = f"余额不足: 可用 {avail_eq:.2f} USDT, 需要保证金 {margin_needed:.2f} USDT ({open_amount:.2f}U合约价值 × {leverage}x杠杆)"
            elif err:
                msg = f"开单失败: {err}"
            else:
                msg = "开单失败（交易所返回错误，请查看日志）"
            self._add_log(f"[测试][{symbol_name}][{timeframe}] {msg}", "error")
            return False, msg

    def test_open_current_price(self, symbol, direction="long", amount_usdt=None):
        """现价直接开单测试（不依赖EMA区间，直接市价开单，用于验证开单链路）"""
        symbol_name = symbol.split("-")[0]
        try:
            ticker = self.client.get_ticker(symbol)
            if not ticker:
                msg = "获取行情失败"
                self._add_log(f"[测试][{symbol_name}] {msg}", "error")
                return False, msg
            current_price = float(ticker["last"])
        except Exception as e:
            msg = f"获取行情异常: {e}"
            self._add_log(f"[测试][{symbol_name}] {msg}", "error")
            return False, msg

        if amount_usdt is None:
            amount_usdt = self._get_total_amount(symbol) / max(self._get_num_entries(symbol), 1)

        leverage = self.config.get("leverage", 10)
        tp_points = self._get_tp_points(symbol)
        sl_points = self._get_sl_points(symbol)

        if direction == "long":
            tp_price = current_price + tp_points
            sl_price = current_price - sl_points
        else:
            tp_price = current_price - tp_points
            sl_price = current_price + sl_points

        side = "buy" if direction == "long" else "sell"
        logger.info(f"[测试][{symbol_name}] 现价开{direction}, 金额{amount_usdt}U, 杠杆{leverage}x, 价格{current_price:.2f}, 止损{sl_price:.2f}, 止盈{tp_price:.2f}")
        self._add_log(f"[测试][{symbol_name}] 现价开{direction}, 金额{amount_usdt}U, 价格{current_price:.2f}, 止损{sl_price:.2f}, 止盈{tp_price:.2f}", "signal")

        try:
            order_id = self.client.place_order_usdt(
                symbol, side, amount_usdt,
                pos_side=direction,
                stop_loss=sl_price,
                take_profit=tp_price,
                leverage=leverage,
            )
        except Exception as e:
            msg = f"下单异常: {e}"
            logger.error(f"[测试][{symbol_name}] {msg}")
            self._add_log(f"[测试][{symbol_name}] {msg}", "error")
            return False, msg

        if order_id:
            self._add_log(f"[测试][{symbol_name}] 开{direction}成功 {amount_usdt}U @ {current_price:.2f}", "success")
            return True, {
                "symbol": symbol,
                "direction": direction,
                "amount": amount_usdt,
                "price": current_price,
                "stop_loss": sl_price,
                "take_profit": tp_price,
                "order_id": order_id,
            }
        else:
            err = getattr(self.client, 'last_error', '')
            balance = self.client.get_balance()
            avail_eq = 0
            if balance:
                for item in balance:
                    for d in item.get("details", []):
                        if d.get("ccy") == "USDT":
                            avail_eq = float(d.get("availEq", "0"))
            margin_needed = amount_usdt / leverage
            if avail_eq > 0 and avail_eq < margin_needed:
                msg = f"余额不足: 可用 {avail_eq:.2f} USDT, 需要保证金 {margin_needed:.2f} USDT ({amount_usdt:.2f}U合约价值 × {leverage}x杠杆)"
            elif err:
                msg = f"开单失败: {err}"
            else:
                msg = "开单失败（交易所返回错误，请查看日志）"
            self._add_log(f"[测试][{symbol_name}] {msg}", "error")
            return False, msg

    def _add_log(self, msg, level="info"):
        now_cn = datetime.now(timezone.utc) + timedelta(hours=8)
        self.signal_logs.append({
            "time": now_cn.strftime("%H:%M:%S"),
            "msg": msg,
            "level": level,
        })
        if len(self.signal_logs) > 200:
            self.signal_logs = self.signal_logs[-200:]


_auto_trader = None
_auto_trader_lock = threading.Lock()


def get_auto_trader(client=None):
    global _auto_trader
    if _auto_trader is None:
        if client is None:
            return None
        with _auto_trader_lock:
            if _auto_trader is None:
                _auto_trader = AutoTrader(client)
    return _auto_trader
