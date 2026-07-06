"""
自动交易引擎 - 基于多周期EMA策略
支持多币种同时监控和交易
"""
from loguru import logger
import threading
import time
from datetime import datetime
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
        }

        self.analysis_cache = {}
        self.signal_logs = []
        self.opened_positions = {}
        self.last_check_time = None
        self._buffer_state = {}
        self.buffer_width = 10
        
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
        }

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self._check_once()
            except Exception as e:
                logger.error(f"检查循环异常: {e}")
                self._add_log(f"检查异常: {e}", "error")
            self._stop_event.wait(30)

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
            candles, source = kline_svc.fetch_klines(timeframe, 300, symbol)
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

        in_zone = analysis.get("in_zone", False)
        if not in_zone:
            position_key_prefix = f"{symbol}_{tf}_"
            keys_to_remove = [k for k in self.opened_positions if k.startswith(position_key_prefix)]
            for k in keys_to_remove:
                del self.opened_positions[k]
            return

        trend = self.strategy.get_trend_direction(analysis_map, tf)
        if not trend or trend == "neutral":
            return

        position_key = f"{symbol}_{tf}_{trend}"
        if position_key in self.opened_positions:
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
            self.opened_positions[position_key] = {
                "symbol": symbol,
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

    def _add_log(self, msg, level="info"):
        self.signal_logs.append({
            "time": datetime.now().strftime("%H:%M:%S"),
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
