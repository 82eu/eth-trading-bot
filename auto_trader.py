"""
自动交易引擎 - 基于多周期EMA策略，支持挂单模式
"""
from loguru import logger
import threading
import time
from datetime import datetime, timezone, timedelta
from ema_strategy import EMAStrategy
from kline_service import get_kline_service
from config import LEVERAGE

BEIJING_TZ = timezone(timedelta(hours=8))


def beijing_now():
    return datetime.now(BEIJING_TZ)


class AutoTrader:
    """自动交易引擎"""

    def __init__(self, client, feishu_bot=None):
        self.client = client
        self.feishu_bot = feishu_bot
        self.strategy = EMAStrategy()
        self.kline_svc = get_kline_service()

        self.running = False
        self._thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        self.symbol_configs = {
            "ETH-USDT-SWAP": {
                "enabled": True,
                "enabled_tfs": ["5m", "15m"],
                "total_amount_usdt": 100,
                "num_entries": 2,
                "tp_points": 50,
                "sl_points": 30,
                "buffer_width": 10,
                "leverage": 100,
                "pending_mode": False,
                "feishu_enabled": True,
            },
            "BTC-USDT-SWAP": {
                "enabled": False,
                "enabled_tfs": ["5m", "15m"],
                "total_amount_usdt": 100,
                "num_entries": 2,
                "tp_points": 500,
                "sl_points": 300,
                "buffer_width": 100,
                "leverage": 100,
                "pending_mode": False,
                "feishu_enabled": True,
            },
        }

        self.analysis_cache = {}
        self.signal_logs = []
        self._buffer_state = {}
        self._pending_orders = {}
        self._last_pending_update = {}
        self.last_check_time = None

    def _notify_feishu(self, msg, msg_type="text"):
        if not self.feishu_bot:
            return
        try:
            symbol = msg.get("symbol", "") if isinstance(msg, dict) else ""
            if symbol and symbol in self.symbol_configs:
                if not self.symbol_configs[symbol].get("feishu_enabled", True):
                    return
            self.feishu_bot.send(msg, msg_type)
        except Exception as e:
            logger.debug(f"飞书通知失败: {e}")

    def _add_log(self, msg, level="info"):
        bj_time = beijing_now().strftime("%H:%M:%S")
        self.signal_logs.append({
            "time": bj_time,
            "msg": msg,
            "level": level,
        })
        if len(self.signal_logs) > 200:
            self.signal_logs = self.signal_logs[-200:]

    def start(self):
        if self.running:
            return
        self.running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("自动交易已启动")
        self._add_log("自动交易已启动", "info")
        self._notify_feishu("自动交易已启动")

    def stop(self):
        if not self.running:
            return
        self.running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        for symbol, cfg in self.symbol_configs.items():
            if cfg.get("enabled") and cfg.get("pending_mode"):
                self._cancel_all_pending_for_symbol(symbol)
        logger.info("自动交易已停止")
        self._add_log("自动交易已停止", "info")
        self._notify_feishu("自动交易已停止")

    def update_symbol_config(self, symbol, new_config):
        if symbol not in self.symbol_configs:
            self.symbol_configs[symbol] = {
                "enabled": False,
                "enabled_tfs": ["5m", "15m"],
                "total_amount_usdt": 100,
                "num_entries": 2,
                "tp_points": 50,
                "sl_points": 30,
                "buffer_width": 10,
                "leverage": 100,
                "pending_mode": False,
                "feishu_enabled": True,
            }
        cfg = self.symbol_configs[symbol]
        for k, v in new_config.items():
            if k in cfg:
                cfg[k] = v
        logger.info(f"{symbol} 配置已更新: {cfg}")

    def get_status(self):
        if not self.analysis_cache:
            try:
                self.refresh_all_analysis()
            except Exception as e:
                logger.debug(f"刷新分析数据失败: {e}")
        return {
            "running": self.running,
            "symbol_configs": self.symbol_configs,
            "analysis": self.analysis_cache,
            "logs": self.signal_logs[-50:],
            "last_check": self.last_check_time.isoformat() if self.last_check_time else None,
            "buffer_state": self._buffer_state,
            "pending_orders": {k: list(v.keys()) for k, v in self._pending_orders.items()},
        }

    def refresh_all_analysis(self):
        all_analysis = {}
        for symbol, cfg in self.symbol_configs.items():
            if not cfg.get("enabled"):
                continue
            symbol_analysis = {}
            for tf in self.strategy.TIMEFRAMES:
                candles = self._get_candles(symbol, tf)
                if candles:
                    analysis = self.strategy.analyze_tf(candles, tf)
                    if analysis:
                        symbol_analysis[tf] = analysis
            all_analysis[symbol] = symbol_analysis
        self.analysis_cache = all_analysis
        self.last_check_time = beijing_now()
        return all_analysis

    def _run(self):
        last_pending_check = 0
        while not self._stop_event.is_set():
            try:
                self._check_once()
            except Exception as e:
                logger.error(f"检查循环异常: {e}")
                self._add_log(f"检查异常: {e}", "error")
            now = time.time()
            if now - last_pending_check >= 300:
                try:
                    self._update_all_pending_orders()
                except Exception as e:
                    logger.error(f"挂单更新异常: {e}")
                last_pending_check = now
            self._stop_event.wait(30)

    def _check_once(self):
        analysis_map = self.refresh_all_analysis()
        for symbol, cfg in self.symbol_configs.items():
            if not cfg.get("enabled"):
                continue
            if cfg.get("pending_mode"):
                continue
            sym_analysis = analysis_map.get(symbol, {})
            enabled_tfs = cfg.get("enabled_tfs", [])
            for tf in enabled_tfs:
                if tf not in sym_analysis:
                    continue
                self._check_tf_signal(symbol, tf, analysis_map)

    def _get_candles(self, symbol, timeframe):
        try:
            candles, source = self.kline_svc.fetch_klines(timeframe, 300, symbol)
            return candles
        except Exception as e:
            logger.error(f"{symbol} {timeframe} 获取K线失败: {e}")
            return None

    def _check_tf_signal(self, symbol, tf, analysis_map):
        sym_analysis = analysis_map.get(symbol, {})
        analysis = sym_analysis.get(tf)
        if not analysis:
            return

        cfg = self.symbol_configs.get(symbol, {})
        buffer_width = cfg.get("buffer_width", 10)
        ema_low = analysis["ema_low"]
        ema_high = analysis["ema_high"]
        current_price = analysis["current_price"]

        buffer_low = round(ema_low - buffer_width, 2)
        buffer_high = round(ema_high + buffer_width, 2)

        state_key = f"{symbol}_{tf}"
        prev_state = self._buffer_state.get(state_key, "unknown")

        in_zone = ema_low <= current_price <= ema_high
        in_buffer = buffer_low <= current_price <= buffer_high

        if not in_buffer and not in_zone:
            if prev_state != "outside":
                self._buffer_state[state_key] = "outside"
            return

        if in_zone:
            self._buffer_state[state_key] = "inside"
        elif in_buffer:
            if prev_state == "inside":
                self._buffer_state[state_key] = "buffer"
                return
            else:
                return

        trend = self.strategy.get_trend_direction(sym_analysis, tf)
        if not trend or trend == "neutral":
            return

        position_key = f"{symbol}_{tf}_{trend}"
        if position_key in self._buffer_state and self._buffer_state[position_key].get("opened"):
            return

        num_entries = cfg.get("num_entries", 2)
        total_amount = cfg.get("total_amount_usdt", 100)
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

        sl_points = cfg.get("sl_points", 30)
        tp_points = cfg.get("tp_points", 50)
        sl_price = self.strategy.calc_stop_loss(analysis, trend, sl_points)
        tp_price = self.strategy.calc_take_profit(analysis, trend, tp_points)

        self._add_log(f"[{symbol}][{tf}] {trend}信号, 入场{entry_index+1}/{num_entries}, 价格{current_price:.2f}", "signal")

        side = "buy" if trend == "long" else "sell"
        leverage = cfg.get("leverage", LEVERAGE)
        order_id = self.client.place_order_usdt(
            symbol, side, open_amount,
            pos_side=trend,
            stop_loss=sl_price,
            take_profit=tp_price,
            leverage=leverage,
        )

        if order_id:
            self._buffer_state[position_key] = {"opened": True, "entry_index": entry_index}
            self._add_log(f"[{symbol}][{tf}] 开{trend}成功 {open_amount}U @ {current_price:.2f}", "success")
            self._notify_feishu(f"{symbol} {tf} {trend} 开单成功 {open_amount}U @ {current_price:.2f}")
        else:
            err = getattr(self.client, "last_error", "未知错误")
            self._add_log(f"[{symbol}][{tf}] 开单失败: {err}", "error")
            self._notify_feishu(f"{symbol} {tf} {trend} 开单失败: {err}")

    def _update_all_pending_orders(self):
        for symbol, cfg in self.symbol_configs.items():
            if not cfg.get("enabled") or not cfg.get("pending_mode"):
                continue
            try:
                self._update_pending_orders_for_symbol(symbol)
            except Exception as e:
                logger.error(f"{symbol} 更新挂单失败: {e}")

    def refresh_pending_manual(self):
        updated = []
        for symbol, cfg in self.symbol_configs.items():
            if not cfg.get("enabled") or not cfg.get("pending_mode"):
                continue
            try:
                self._update_pending_orders_for_symbol(symbol)
                updated.append(symbol)
            except Exception as e:
                logger.error(f"{symbol} 手动刷新挂单失败: {e}")
        return updated

    def _cancel_all_pending_for_symbol(self, symbol):
        key = symbol
        order_map = self._pending_orders.get(key, {})
        for ord_id in list(order_map.keys()):
            try:
                self.client.cancel_order(symbol, ord_id)
            except Exception as e:
                logger.debug(f"撤单失败 {ord_id}: {e}")
        self._pending_orders[key] = {}

    def _update_pending_orders_for_symbol(self, symbol):
        cfg = self.symbol_configs.get(symbol, {})
        if not cfg.get("enabled") or not cfg.get("pending_mode"):
            return

        self._cancel_all_pending_for_symbol(symbol)

        candles_5m = self._get_candles(symbol, "5m")
        if not candles_5m:
            return
        current_price = float(candles_5m[-1][4])

        sym_analysis = {}
        for tf in self.strategy.TIMEFRAMES:
            candles = self._get_candles(symbol, tf)
            if candles:
                a = self.strategy.analyze_tf(candles, tf)
                if a:
                    sym_analysis[tf] = a

        enabled_tfs = cfg.get("enabled_tfs", [])
        num_entries = cfg.get("num_entries", 1)
        total_amount = cfg.get("total_amount_usdt", 100)
        amount_per_entry = total_amount / num_entries
        tp_points = cfg.get("tp_points", 50)
        sl_points = cfg.get("sl_points", 30)
        leverage = cfg.get("leverage", LEVERAGE)

        new_orders = {}

        for tf in enabled_tfs:
            analysis = sym_analysis.get(tf)
            if not analysis:
                continue

            trend = self.strategy.get_trend_direction(sym_analysis, tf)
            if not trend or trend == "neutral":
                continue

            entries = self.strategy.calc_entry_levels(analysis, trend, num_entries)
            sl_price = self.strategy.calc_stop_loss(analysis, trend, sl_points)

            for i, entry in enumerate(entries):
                entry_price = entry["price"]
                tp_price = entry_price + tp_points if trend == "long" else entry_price - tp_points

                if trend == "long" and entry_price >= current_price:
                    logger.info(f"[{symbol}][{tf}] 做多挂单价{entry_price:.2f} >= 现价{current_price:.2f}，跳过")
                    self._add_log(f"[{symbol}][{tf}] 做多挂单价{entry_price:.2f} >= 现价，跳过", "info")
                    continue
                if trend == "short" and entry_price <= current_price:
                    logger.info(f"[{symbol}][{tf}] 做空挂单价{entry_price:.2f} <= 现价{current_price:.2f}，跳过")
                    self._add_log(f"[{symbol}][{tf}] 做空挂单价{entry_price:.2f} <= 现价，跳过", "info")
                    continue

                side = "buy" if trend == "long" else "sell"
                try:
                    order_id = self.client.place_order_usdt(
                        symbol, side, amount_per_entry,
                        order_type="limit",
                        price=entry_price,
                        pos_side=trend,
                        stop_loss=sl_price,
                        take_profit=tp_price,
                        leverage=leverage,
                    )
                except Exception as e:
                    err_msg = str(e)
                    if "51006" in err_msg or "Price" in err_msg and "limit" in err_msg:
                        logger.info(f"[{symbol}][{tf}] 挂单价格偏离过大暂不挂: {entry_price:.2f}")
                        self._add_log(f"[{symbol}][{tf}] 价格偏离过大暂不挂: {entry_price:.2f}", "info")
                    else:
                        logger.error(f"[{symbol}][{tf}] 挂单失败: {e}")
                        self._add_log(f"[{symbol}][{tf}] 挂单失败: {e}", "error")
                    continue

                if order_id:
                    new_orders[order_id] = {
                        "tf": tf,
                        "side": trend,
                        "entry_index": i,
                        "price": entry_price,
                        "amount": amount_per_entry,
                    }
                    self._add_log(
                        f"[{symbol}][{tf}] 挂{trend}单 {i+1}/{num_entries} @ {entry_price:.2f}",
                        "info"
                    )

        self._pending_orders[symbol] = new_orders
        self._last_pending_update[symbol] = beijing_now()

    def test_open_order(self, symbol, timeframe, direction=None):
        candles = self._get_candles(symbol, timeframe)
        if not candles:
            msg = f"获取{timeframe}K线失败"
            logger.error(msg)
            self._add_log(f"[测试][{symbol}][{timeframe}] {msg}", "error")
            return False, msg

        analysis = self.strategy.analyze_tf(candles, timeframe)
        if not analysis:
            msg = "策略分析失败"
            logger.error(msg)
            self._add_log(f"[测试][{symbol}][{timeframe}] {msg}", "error")
            return False, msg

        self.refresh_all_analysis()
        sym_analysis = self.analysis_cache.get(symbol, {})
        if not sym_analysis:
            sym_analysis = {timeframe: analysis}

        if direction is None:
            direction = self.strategy.get_trend_direction(sym_analysis, timeframe)
            if not direction or direction == "neutral":
                msg = "无法判断趋势"
                self._add_log(f"[测试][{symbol}][{timeframe}] {msg}", "error")
                return False, msg

        cfg = self.symbol_configs.get(symbol, {})
        total_amount = cfg.get("total_amount_usdt", 100)
        sl_points = cfg.get("sl_points", 30)
        tp_points = cfg.get("tp_points", 50)
        leverage = cfg.get("leverage", LEVERAGE)

        sl_price = self.strategy.calc_stop_loss(analysis, direction, sl_points)
        tp_price = self.strategy.calc_take_profit(analysis, direction, tp_points)

        current_price = analysis["current_price"]
        logger.info(f"[测试][{symbol}][{timeframe}] 开{direction}, 金额{total_amount}U, 杠杆{leverage}x, 价格{current_price:.2f}, 止损{sl_price:.2f}, 止盈{tp_price:.2f}")
        self._add_log(f"[测试][{symbol}][{timeframe}] 开{direction}, 金额{total_amount}U, 价格{current_price:.2f}, 止损{sl_price:.2f}, 止盈{tp_price:.2f}", "signal")

        side = "buy" if direction == "long" else "sell"
        try:
            order_id = self.client.place_order_usdt(
                symbol, side, total_amount,
                pos_side=direction,
                stop_loss=sl_price,
                take_profit=tp_price,
                leverage=leverage,
            )
        except Exception as e:
            msg = f"下单异常: {e}"
            logger.error(f"[测试][{symbol}][{timeframe}] {msg}")
            self._add_log(f"[测试][{symbol}][{timeframe}] {msg}", "error")
            return False, msg

        if order_id:
            self._add_log(f"[测试][{symbol}][{timeframe}] 开{direction}成功 {total_amount}U", "success")
            self._notify_feishu(f"[测试] {symbol} {timeframe} {direction} 开单成功 {total_amount}U")
            return True, {
                "direction": direction,
                "amount": total_amount,
                "price": current_price,
                "stop_loss": sl_price,
                "take_profit": tp_price,
                "order_id": order_id,
            }
        else:
            err = getattr(self.client, "last_error", "未知错误")
            msg = f"开单失败: {err}"
            self._add_log(f"[测试][{symbol}][{timeframe}] {msg}", "error")
            return False, msg


_auto_trader = None
_auto_trader_lock = threading.Lock()


def get_auto_trader(client=None, feishu_bot=None):
    global _auto_trader
    if _auto_trader is None:
        if client is None:
            return None
        with _auto_trader_lock:
            if _auto_trader is None:
                _auto_trader = AutoTrader(client, feishu_bot)
    return _auto_trader
