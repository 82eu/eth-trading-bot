"""
EMA双均线策略模块
- 多周期 (5m/15m/30m/1h/4h)
- EMA 180 / EMA 250
- 价格进入区间开单
- 分批建仓 (1/2/3份)
- 大周期趋势过滤
- 止损：离价格最远的EMA + 固定点数；小周期止损落在大周期区间内时缩减为1/3
"""
from loguru import logger
import math


class EMAStrategy:
    """EMA双均线策略"""

    TIMEFRAMES = ["5m", "15m", "30m", "1h", "4h"]

    def __init__(self):
        self.ema_fast = 180
        self.ema_slow = 250
        self.zone_width_threshold_pct = 0.2

    def calculate_ema(self, prices, period):
        """计算EMA"""
        if len(prices) < period:
            return None
        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period  # 初始SMA
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def _compute_ema_list(self, prices, period):
        if len(prices) < period:
            return None
        multiplier = 2 / (period + 1)
        ema_list = []
        prev_ema = sum(prices[:period]) / period
        for i, price in enumerate(prices):
            if i < period:
                ema_list.append(None)
            else:
                cur = (price - prev_ema) * multiplier + prev_ema
                ema_list.append(cur)
                prev_ema = cur
        return ema_list

    def _detect_entanglement(self, ema_fast_list, ema_slow_list):
        crosses = 0
        min_interval = 999999
        last_cross_idx = -1
        recent_crosses = 0
        start_idx = max(0, len(ema_fast_list) - 100)
        for i in range(1, len(ema_fast_list)):
            if ema_fast_list[i] is None or ema_fast_list[i-1] is None:
                continue
            if ema_slow_list[i] is None or ema_slow_list[i-1] is None:
                continue
            prev_diff = ema_fast_list[i-1] - ema_slow_list[i-1]
            cur_diff = ema_fast_list[i] - ema_slow_list[i]
            if prev_diff * cur_diff < 0:
                crosses += 1
                if last_cross_idx >= 0:
                    interval = i - last_cross_idx
                    if interval < min_interval:
                        min_interval = interval
                last_cross_idx = i
                if i >= start_idx:
                    recent_crosses += 1
        is_entangled = (crosses >= 3 and min_interval <= 30) or (recent_crosses >= 3)
        return is_entangled, crosses, min_interval, recent_crosses

    def analyze_tf(self, candles, timeframe):
        """
        分析单个周期（含纠缠判断）
        """
        closes = [float(c[4]) for c in candles]
        current_price = closes[-1]

        ema180_list = self._compute_ema_list(closes, self.ema_fast)
        ema250_list = self._compute_ema_list(closes, self.ema_slow)

        if ema180_list is None or ema250_list is None:
            return None

        ema180 = ema180_list[-1]
        ema250 = ema250_list[-1]

        if ema180 is None or ema250 is None:
            return None

        ema_high = max(ema180, ema250)
        ema_low = min(ema180, ema250)
        center = (ema_high + ema_low) / 2
        zone_width = ema_high - ema_low
        zone_width_pct = (zone_width / current_price) * 100

        in_zone = ema_low <= current_price <= ema_high

        if ema180 > ema250:
            arrangement = "long"
        else:
            arrangement = "short"

        is_ranging = zone_width_pct < self.zone_width_threshold_pct

        is_entangled, cross_count, min_interval, recent_crosses = self._detect_entanglement(ema180_list, ema250_list)

        if current_price > ema_high:
            trend = "long"
        elif current_price < ema_low:
            trend = "short"
        else:
            trend = arrangement

        return {
            "tf": timeframe,
            "ema180": ema180,
            "ema250": ema250,
            "current_price": current_price,
            "in_zone": in_zone,
            "trend": trend,
            "arrangement": arrangement,
            "ema_high": ema_high,
            "ema_low": ema_low,
            "center_price": center,
            "zone_width": zone_width,
            "zone_width_pct": zone_width_pct,
            "is_ranging": is_ranging,
            "is_entangled": is_entangled,
            "cross_count": cross_count,
            "min_interval": min_interval,
            "recent_crosses": recent_crosses,
        }

    def get_trend_direction(self, analysis_map, timeframe):
        """
        获取趋势方向（EMA纠缠时递推到大周期）
        从起始周期开始，若当前周期EMA纠缠（交叉太近）则向上递推
        直到找到非纠缠周期，以其趋势为准
        5分钟周期优先用价格位置判断，不递推
        """
        tf_order = self.TIMEFRAMES
        if timeframe not in tf_order:
            return "neutral"
        idx = tf_order.index(timeframe)

        for i in range(idx, len(tf_order)):
            tf = tf_order[i]
            if tf in analysis_map and analysis_map[tf]:
                a = analysis_map[tf]
                if not a.get("is_entangled", False):
                    return a["trend"]

        if timeframe in analysis_map and analysis_map[timeframe]:
            return analysis_map[timeframe]["trend"]
        return "neutral"

    def calc_entry_levels(self, analysis, direction, num_entries=1):
        """
        计算分批建仓点位
        固定以EMA180（快线）为入口，EMA250（慢线）为出口
        :param analysis: 单周期分析结果
        :param direction: 'long' / 'short'
        :param num_entries: 1/2/3 份
        :return: [{'index': 1, 'price': float, 'description': '...'}, ...]
        """
        ema180 = analysis["ema180"]
        ema250 = analysis["ema250"]
        center = (ema180 + ema250) / 2

        entries = []
        if num_entries == 1:
            entries.append({"index": 1, "price": ema180, "description": "入口EMA(EMA180)"})
        elif num_entries == 2:
            entries.append({"index": 1, "price": ema180, "description": "入口EMA(EMA180)"})
            entries.append({"index": 2, "price": center, "description": "中心线"})
        elif num_entries == 3:
            entries.append({"index": 1, "price": ema180, "description": "入口EMA(EMA180)"})
            entries.append({"index": 2, "price": center, "description": "中心线"})
            entries.append({"index": 3, "price": ema250, "description": "出口EMA(EMA250)"})

        return entries

    def calc_stop_loss(self, analysis, direction, sl_points, current_entry_price=None):
        """
        计算止损价
        逻辑：以EMA250（慢线）为基准，加上固定止损点数
        - 做多：止损 = EMA250 - 止损点数
        - 做空：止损 = EMA250 + 止损点数
        :param analysis: 单周期分析结果
        :param direction: 'long' / 'short'
        :param sl_points: 止损点数（价格点数）
        :param current_entry_price: 当前入场价（None用现价）
        :return: 止损价
        """
        ema250 = analysis["ema250"]

        if direction == "long":
            stop_loss = ema250 - sl_points
        else:
            stop_loss = ema250 + sl_points

        return stop_loss

    def check_small_tf_sl_in_big_zone(self, small_analysis, big_analysis, direction, sl_points):
        """
        检查小周期止损是否落在大周期EMA区间内
        如果落在区间内（说明两周期EMA接近或价格剧烈波动），止损距离缩减为原来的1/3
        :return: (是否在区间内, 调整后的止损价)
        """
        sl_price = self.calc_stop_loss(small_analysis, direction, sl_points)
        big_ema_high = big_analysis["ema_high"]
        big_ema_low = big_analysis["ema_low"]

        in_zone = big_ema_low <= sl_price <= big_ema_high

        if in_zone:
            current_price = small_analysis["current_price"]
            sl_distance = abs(current_price - sl_price)
            new_sl_distance = sl_distance / 3

            if direction == "long":
                new_sl = current_price - new_sl_distance
            else:
                new_sl = current_price + new_sl_distance

            return True, new_sl

        return False, sl_price

    def calc_take_profit(self, analysis, direction, tp_points, current_entry_price=None):
        """
        计算止盈价
        止盈 = 入场价 ± 止盈点数
        :param analysis: 单周期分析结果
        :param direction: 'long' / 'short'
        :param tp_points: 止盈点数（价格点数）
        :param current_entry_price: 当前入场价（None用现价）
        :return: 止盈价
        """
        price = current_entry_price or analysis["current_price"]

        if direction == "long":
            take_profit = price + tp_points
        else:
            take_profit = price - tp_points

        return take_profit
