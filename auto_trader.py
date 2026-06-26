"""
自动交易引擎
- 多周期 EMA 策略自动开单
- 分批建仓
- 止盈止损
"""
from loguru import logger
import threading
import time
from datetime import datetime
from ema_strategy import EMAStrategy


class AutoTrader:
    """自动交易引擎"""

    def __init__(self, okx_client, symbol="ETH-USDT-SWAP"):
        self.client = okx_client
        self.symbol = symbol
        self.strategy = EMAStrategy()
        self.running = False
        self._thread = None

        self.config = {
            "enabled_tfs": ["5m", "15m"],
            "total_amount_usdt": 100,
            "num_entries": 2,
            "tp_points": 50,
            "sl_points": 30,
        }

        self.positions = {}
        self.analysis_cache = {}
        self.signal_log = []
        self.last_check = None

        for tf in self.strategy.TIMEFRAMES:
            self.positions[tf] = {
                "direction": None,
                "entries_done": 0,
                "entry_prices": [],
                "stop_loss": None,
                "take_profit": None,
                "status": "idle",
            }

    def set_config(self, **kwargs):
        """更新配置"""
        for k, v in kwargs.items():
            if k in self.config:
                self.config[k] = v
                logger.info(f"配置更新: {k} = {v}")

    def start(self):
        """启动自动交易"""
        if self.running:
            return False
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("自动交易引擎已启动")
        self._add_log("系统启动", "info")
        return True

    def stop(self):
        """停止自动交易"""
        self.running = False
        logger.info("自动交易引擎已停止")
        self._add_log("系统停止", "info")
        return True

    def _run_loop(self):
        """主循环"""
        while self.running:
            try:
                self._check_once()
            except Exception as e:
                logger.error(f"检查异常: {e}")
            for _ in range(30):
                if not self.running:
                    break
                time.sleep(1)

    def _check_once(self):
        """执行一次检查"""
        self.last_check = datetime.now()

        analysis_map = {}
        for tf in self.strategy.TIMEFRAMES:
            try:
                candles = self._get_candles(tf)
                if candles:
                    analysis = self.strategy.analyze_tf(candles, tf)
                    analysis_map[tf] = analysis
            except Exception as e:
                logger.error(f"获取{tf}K线失败: {e}")

        self.analysis_cache = analysis_map

        enabled_tfs = self.config.get("enabled_tfs", [])
        for tf in enabled_tfs:
            if tf not in analysis_map:
                continue
            try:
                self._check_tf_signal(tf, analysis_map)
            except Exception as e:
                logger.error(f"检查{tf}信号失败: {e}")

    def _get_candles(self, timeframe):
        """获取K线"""
        try:
            result = self.client.get_candles(self.symbol, timeframe, limit=300)
            if result and isinstance(result, list):
                return result
        except Exception as e:
            logger.error(f"获取K线失败 {timeframe}: {e}")
        return None

    def _check_tf_signal(self, tf, analysis_map):
        """检查单个周期信号"""
        analysis = analysis_map.get(tf)
        if not analysis:
            return

        pos = self.positions.get(tf)
        if not pos:
            return

        if pos["status"] != "idle":
            return

        if not analysis["in_zone"]:
            return

        direction = self.strategy.get_trend_direction(analysis_map, tf)
        if direction == "neutral":
            return

        entries = self.strategy.calc_entry_levels(
            analysis, direction, self.config["num_entries"]
        )

        current_price = analysis["current_price"]
        num_entries = self.config["num_entries"]
        total_amount = self.config["total_amount_usdt"]
        per_entry_amount = total_amount / num_entries

        triggered_entries = []
        for entry in entries:
            entry_price = entry["price"]
            if direction == "long":
                if current_price <= entry_price * 1.001:
                    triggered_entries.append(entry)
            else:
                if current_price >= entry_price * 0.999:
                    triggered_entries.append(entry)

        if not triggered_entries:
            return

        tp_points = self.config["tp_points"]
        sl_points = self.config["sl_points"]

        sl_price = self.strategy.calc_stop_loss(analysis, direction, sl_points, current_price)
        tp_price = self.strategy.calc_take_profit(analysis, direction, tp_points, current_price)

        tf_order = self.strategy.TIMEFRAMES
        tf_idx = tf_order.index(tf)
        sl_adjusted = False
        for i in range(tf_idx + 1, len(tf_order)):
            big_tf = tf_order[i]
            if big_tf in analysis_map and analysis_map[big_tf]:
                in_big_zone, new_sl = self.strategy.check_small_tf_sl_in_big_zone(
                    analysis, analysis_map[big_tf], direction, sl_points
                )
                if in_big_zone:
                    sl_price = new_sl
                    sl_adjusted = True
                    logger.info(f"{tf} 止损在{big_tf}区间内，止损调整为 1/3: {sl_price:.2f}")
                    break

        total_triggered = len(triggered_entries)
        open_amount = per_entry_amount * total_triggered

        logger.info(
            f"[{tf}] 信号触发: {direction}, 价格: {current_price:.2f}, "
            f"触发{total_triggered}份, 金额: {open_amount:.2f}U, "
            f"止损: {sl_price:.2f}, 止盈: {tp_price:.2f}"
            + (" (止损已缩1/3)" if sl_adjusted else "")
        )

        try:
            side = "buy" if direction == "long" else "sell"
            order_id = self.client.place_order_usdt(
                self.symbol, side, open_amount,
                pos_side=direction,
                stop_loss=sl_price,
                take_profit=tp_price
            )

            if order_id:
                pos["status"] = "open"
                pos["direction"] = direction
                pos["entries_done"] = total_triggered
                pos["entry_prices"] = [current_price] * total_triggered
                pos["stop_loss"] = sl_price
                pos["take_profit"] = tp_price
                pos["sl_adjusted"] = sl_adjusted

                log_msg = f"[{tf}] 开{direction} {open_amount:.0f}U @ {current_price:.2f}"
                if sl_adjusted:
                    log_msg += " (止损缩1/3)"
                self._add_log(log_msg, "success" if direction == "long" else "danger")
            else:
                self._add_log(f"[{tf}] 开单失败", "error")
        except Exception as e:
            logger.error(f"开单异常: {e}")
            self._add_log(f"[{tf}] 开单异常: {str(e)}", "error")

    def _add_log(self, message, level="info"):
        """添加日志"""
        self.signal_log.insert(0, {
            "time": datetime.now().strftime("%H:%M:%S"),
            "message": message,
            "level": level,
        })
        if len(self.signal_log) > 100:
            self.signal_log = self.signal_log[:100]

    def test_open_order(self, tf="5m", force_direction=None):
        """
        测试开单 - 手动触发一次EMA策略开单
        :param tf: 测试哪个周期
        :param force_direction: 强制方向 long/short，不传则按策略趋势判断
        :return: {success, msg, ...}
        """
        try:
            candles = self._get_candles(tf)
            if not candles:
                return {"success": False, "msg": f"获取{tf}K线失败"}

            analysis = self.strategy.analyze_tf(candles, tf)
            if not analysis:
                return {"success": False, "msg": "策略分析失败，K线数据不足"}

            analysis_map = {}
            for t in self.strategy.TIMEFRAMES:
                try:
                    c = self._get_candles(t)
                    if c:
                        analysis_map[t] = self.strategy.analyze_tf(c, t)
                except Exception:
                    pass
            if tf not in analysis_map:
                analysis_map[tf] = analysis

            direction = force_direction or self.strategy.get_trend_direction(analysis_map, tf)
            if direction == "neutral":
                return {"success": False, "msg": "无法判断趋势方向"}

            current_price = analysis["current_price"]
            num_entries = self.config["num_entries"]
            total_amount = self.config["total_amount_usdt"]
            tp_points = self.config["tp_points"]
            sl_points = self.config["sl_points"]

            entries = self.strategy.calc_entry_levels(analysis, direction, num_entries)
            sl_price = self.strategy.calc_stop_loss(analysis, direction, sl_points, current_price)
            tp_price = self.strategy.calc_take_profit(analysis, direction, tp_points, current_price)

            tf_order = self.strategy.TIMEFRAMES
            tf_idx = tf_order.index(tf) if tf in tf_order else 0
            sl_adjusted = False
            for i in range(tf_idx + 1, len(tf_order)):
                big_tf = tf_order[i]
                if big_tf in analysis_map and analysis_map[big_tf]:
                    in_big_zone, new_sl = self.strategy.check_small_tf_sl_in_big_zone(
                        analysis, analysis_map[big_tf], direction, sl_points
                    )
                    if in_big_zone:
                        sl_price = new_sl
                        sl_adjusted = True
                        break

            per_entry_amount = total_amount / num_entries
            open_amount = total_amount

            side = "buy" if direction == "long" else "sell"
            order_id = self.client.place_order_usdt(
                self.symbol, side, open_amount,
                pos_side=direction,
                stop_loss=sl_price,
                take_profit=tp_price
            )

            if order_id:
                pos = self.positions.get(tf)
                if pos:
                    pos["status"] = "open"
                    pos["direction"] = direction
                    pos["entries_done"] = num_entries
                    pos["entry_prices"] = [current_price] * num_entries
                    pos["stop_loss"] = sl_price
                    pos["take_profit"] = tp_price
                    pos["sl_adjusted"] = sl_adjusted

                log_msg = f"[测试][{tf}] 开{direction} {open_amount:.0f}U @ {current_price:.2f}, 止损:{sl_price:.2f}, 止盈:{tp_price:.2f}"
                if sl_adjusted:
                    log_msg += " (止损缩1/3)"
                self._add_log(log_msg, "success" if direction == "long" else "danger")
                logger.info(log_msg)

                return {
                    "success": True,
                    "order_id": order_id,
                    "tf": tf,
                    "direction": direction,
                    "current_price": current_price,
                    "stop_loss": sl_price,
                    "take_profit": tp_price,
                    "amount": open_amount,
                    "sl_adjusted": sl_adjusted,
                    "entry_levels": entries,
                }
            else:
                self._add_log(f"[测试][{tf}] 开单失败", "error")
                return {"success": False, "msg": "开单失败"}

        except Exception as e:
            logger.error(f"测试开单异常: {e}")
            self._add_log(f"[测试] 异常: {str(e)}", "error")
            return {"success": False, "msg": str(e)}

    def get_status(self):
        """获取状态"""
        return {
            "running": self.running,
            "config": self.config,
            "positions": self.positions,
            "analysis": self.analysis_cache,
            "signals": self.signal_log[:20],
            "last_check": self.last_check.strftime("%H:%M:%S") if self.last_check else None,
        }
