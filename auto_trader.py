"""
自动交易引擎 - 基于多周期EMA策略
"""
from loguru import logger
import threading
import time
from datetime import datetime
from ema_strategy import EMAStrategy
from kline_service import get_kline_service
from config import LEVERAGE, SYMBOL


try:
    from config import MOCK_MODE
except ImportError:
    MOCK_MODE = False


class AutoTrader:
    """自动交易引擎"""

    def __init__(self, client):
        self.client = client
        self.symbol = SYMBOL
        self.strategy = EMAStrategy()
        self.kline_svc = get_kline_service()

        self.running = False
        self._thread = None
        self._stop_event = threading.Event()

        self.config = {
            "enabled_tfs": ["5m", "15m"],
            "total_amount_usdt": 100,
            "num_entries": 2,
            "tp_points": 50,
            "sl_points": 30,
            "leverage": LEVERAGE,
        }

        self.analysis_cache = {}
        self.signal_logs = []
        self.opened_positions = {}
        self.last_check_time = None

    def start(self):
        if self.running:
            return
        self.running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("自动交易已启动")
        self._add_log("自动交易已启动", "info")

    def stop(self):
        if not self.running:
            return
        self.running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("自动交易已停止")
        self._add_log("自动交易已停止", "info")

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
        }

    def refresh_analysis(self):
        analysis_map = {}
        for tf in self.strategy.TIMEFRAMES:
            candles = self._get_candles(tf)
            if candles:
                analysis = self.strategy.analyze_tf(candles, tf)
                if analysis:
                    analysis_map[tf] = analysis
        self.analysis_cache = analysis_map
        self.last_check_time = datetime.now()
        return analysis_map

    def _run(self):
        while not self._stop_event.is_set():
            try:
                self._check_once()
            except Exception as e:
                logger.error(f"检查循环异常: {e}")
                self._add_log(f"检查异常: {e}", "error")
            self._stop_event.wait(30)

    def _check_once(self):
        analysis_map = self.refresh_analysis()

        enabled_tfs = self.config.get("enabled_tfs", [])
        for tf in enabled_tfs:
            if tf not in analysis_map:
                continue
            self._check_tf_signal(tf, analysis_map)

    def _get_candles(self, timeframe):
        try:
            candles, source = self.kline_svc.fetch_klines(timeframe, 300, self.symbol)
            return candles
        except Exception as e:
            logger.error(f"获取{timeframe}K线失败: {e}")
            return None

    def _check_tf_signal(self, tf, analysis_map):
        analysis = analysis_map.get(tf)
        if not analysis:
            return

        in_zone = analysis.get("in_zone", False)
        if not in_zone:
            if tf in self.opened_positions:
                del self.opened_positions[tf]
            return

        trend = self.strategy.get_trend_direction(analysis_map, tf)
        if not trend:
            return

        position_key = f"{tf}_{trend}"
        if position_key in self.opened_positions:
            return

        num_entries = self.config.get("num_entries", 2)
        total_amount = self.config.get("total_amount_usdt", 100)
        open_amount = total_amount / num_entries

        entry_points = self.strategy.calc_entry_points(analysis, trend, num_entries)
        current_price = analysis["price"]

        entry_index = -1
        for i, point in enumerate(entry_points):
            if trend == "long":
                if current_price <= point:
                    entry_index = i
            else:
                if current_price >= point:
                    entry_index = i

        if entry_index < 0:
            return

        sl_price = self.strategy.calc_stop_loss(analysis_map, tf, trend, self.config["sl_points"])
        tp_price = self.strategy.calc_take_profit(analysis, trend, self.config["tp_points"])

        self._add_log(f"[{tf}] {trend}信号, 入场{entry_index+1}/{num_entries}, 价格{current_price:.2f}", "signal")

        side = "buy" if trend == "long" else "sell"
        order_id = self.client.place_order_usdt(
            self.symbol, side, open_amount,
            pos_side=trend,
            stop_loss=sl_price,
            take_profit=tp_price,
            leverage=self.config["leverage"],
        )

        if order_id:
            self.opened_positions[position_key] = {
                "entry_index": entry_index,
                "price": current_price,
                "time": datetime.now().isoformat(),
                "order_id": order_id,
            }
            self._add_log(f"[{tf}] 开{trend}成功 {open_amount}U @ {current_price:.2f}", "success")
        else:
            self._add_log(f"[{tf}] 开单失败", "error")

    def test_open_order(self, timeframe, direction=None):
        candles = self._get_candles(timeframe)
        if not candles:
            return False, f"获取{timeframe}K线失败"

        analysis = self.strategy.analyze_tf(candles, timeframe)
        if not analysis:
            return False, "策略分析失败"

        analysis_map = self.refresh_analysis() or {timeframe: analysis}

        if direction is None:
            direction = self.strategy.get_trend_direction(analysis_map, timeframe)
            if not direction:
                return False, "无法判断趋势"

        total_amount = self.config.get("total_amount_usdt", 100)
        num_entries = self.config.get("num_entries", 1)
        open_amount = total_amount / num_entries

        sl_price = self.strategy.calc_stop_loss(analysis_map, timeframe, direction, self.config["sl_points"])
        tp_price = self.strategy.calc_take_profit(analysis, direction, self.config["tp_points"])

        self._add_log(f"[测试][{timeframe}] 测试开{direction}, 金额{open_amount}U, 止损{sl_price:.2f}, 止盈{tp_price:.2f}", "signal")

        side = "buy" if direction == "long" else "sell"
        order_id = self.client.place_order_usdt(
            self.symbol, side, open_amount,
            pos_side=direction,
            stop_loss=sl_price,
            take_profit=tp_price,
            leverage=self.config["leverage"],
        )

        if order_id:
            self._add_log(f"[测试][{timeframe}] 开{direction}成功 {open_amount}U", "success")
            return True, {
                "direction": direction,
                "amount": open_amount,
                "price": analysis["price"],
                "stop_loss": sl_price,
                "take_profit": tp_price,
                "order_id": order_id,
            }
        else:
            self._add_log(f"[测试][{timeframe}] 开单失败", "error")
            return False, "开单失败"

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
