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
        ema = sum(prices[:period]) / period
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def analyze_tf(self, candles, timeframe):
        """
        分析单个周期
        :param candles: K线数组 [[ts, open, high, low, close, vol], ...]
        :param timeframe: 周期
        :return: 分析结果字典
        """
        closes = [float(c[4]) for c in candles]
        current_price = closes[-1]

        ema180 = self.calculate_ema(closes, self.ema_fast)
        ema250 = self.calculate_ema(closes, self.ema_slow)

        if ema180 is None or ema250 is None:
            return None

        ema_high = max(ema180, ema250)
        ema_low = min(ema180, ema250)
        center = (ema_high + ema_low) / 2
        zone_width = ema_high - ema_low
        zone_width_pct = (zone_width / current_price) * 100

        in_zone = ema_low <= current_price <= ema_high

        if ema180 > ema250:
            trend = "long"
        else:
            trend = "short"

        is_ranging = zone_width_pct < self.zone_width_threshold_pct

        ema180_history = []
        ema250_history = []
        for i in range(len(closes)):
            e180 = self.calculate_ema(closes[:i+1], self.ema_fast)
            e250 = self.calculate_ema(closes[:i+1], self.ema_slow)
            if e180 and e250:
                ema180_history.append(e180)
                ema250_history.append(e250)

        crossing_count = 0
        min_cross_distance = float('inf')
        last_cross_idx = -1
        for i in range(1, len(ema180_history)):
            prev_diff = ema180_history[i-1] - ema250_history[i-1]
            curr_diff = ema180_history[i] - ema250_history[i]
            if prev_diff * curr_diff < 0:
                crossing_count += 1
                if last_cross_idx >= 0:
                    distance = i - last_cross_idx
                    if distance < min_cross_distance:
                        min_cross_distance = distance
                last_cross_idx = i

        lookback_periods = min(100, len(ema180_history))
        recent_crosses = 0
        if len(ema180_history) >= lookback_periods:
            for i in range(len(ema180_history) - lookback_periods, len(ema180_history) - 1):
                prev_diff = ema180_history[i] - ema250_history[i]
                curr_diff = ema180_history[i+1] - ema250_history[i+1]
                if prev_diff * curr_diff < 0:
                    recent_crosses += 1

        is_entangled = False
        if crossing_count >= 3 and min_cross_distance <= 30:
            is_entangled = True
        elif recent_crosses >= 2:
            is_entangled = True

        return {
            "tf": timeframe,
            "ema180": ema180,
            "ema250": ema250,
            "current_price": current_price,
            "in_zone": in_zone,
            "trend": trend,
            "ema_high": ema_high,
            "ema_low": ema_low,
            "center_price": center,
            "zone_width": zone_width,
            "zone_width_pct": zone_width_pct,
            "is_ranging": is_ranging,
            "is_entangled": is_entangled,
            "crossing_count": crossing_count,
            "recent_crosses": recent_crosses,
        }

    def get_trend_direction(self, analysis_map, timeframe):
        """
        获取趋势方向（仅EMA线纠缠时递推到大周期）
        从起始周期开始，若当前周期EMA180和EMA250纠缠在一起（反复交叉，交点距离太近），则向上递推
        直到找到EMA线分离的周期，以其趋势为准
        每个周期用自己的EMA位置挂单，趋势方向可以参考大周期
        :param analysis_map: {tf: analysis_result}
        :param timeframe: 起始周期
        :return: 'long' / 'short' / 'neutral'
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

    def get_entry_side(self, analysis, direction):
        """
        固定入场方向：
        - 做多：从区间上沿(ema_high)往下跌着入场
        - 做空：从区间下沿(ema_low)往上涨着入场
        :param analysis: 单周期分析结果
        :param direction: 'long' / 'short'
        :return: (entry_ema, exit_ema) 入口EMA线、出口EMA线
        """
        ema_high = analysis["ema_high"]
        ema_low = analysis["ema_low"]

        if direction == "long":
            return ema_high, ema_low
        else:
            return ema_low, ema_high

    def calc_entry_levels(self, analysis, direction, num_entries=1):
        """
        计算分批建仓点位
        :param analysis: 单周期分析结果
        :param direction: 'long' / 'short'
        :param num_entries: 1/2/3 份
        :return: [{'index': 1, 'price': float, 'description': '...'}, ...]
        """
        ema_high = analysis["ema_high"]
        ema_low = analysis["ema_low"]
        center = (ema_high + ema_low) / 2
        entry_line, exit_line = self.get_entry_side(analysis, direction)

        entries = []
        if num_entries == 1:
            entries.append({"index": 1, "price": center, "description": "中心线"})
        elif num_entries == 2:
            entries.append({"index": 1, "price": entry_line, "description": "入口EMA"})
            entries.append({"index": 2, "price": center, "description": "中心线"})
        elif num_entries == 3:
            entries.append({"index": 1, "price": entry_line, "description": "入口EMA"})
            entries.append({"index": 2, "price": center, "description": "中心线"})
            entries.append({"index": 3, "price": exit_line, "description": "出口EMA"})

        return entries

    def calc_stop_loss(self, analysis, direction, sl_points, current_entry_price=None):
        """
        计算止损价
        逻辑：以离价格最远的那条EMA线为基准，加上固定止损点数
        - 做多：止损 = 区间下沿(ema_low) - 止损点数
        - 做空：止损 = 区间上沿(ema_high) + 止损点数
        :param analysis: 单周期分析结果
        :param direction: 'long' / 'short'
        :param sl_points: 止损点数（价格点数）
        :param current_entry_price: 当前入场价（None用现价）
        :return: 止损价
        """
        ema_high = analysis["ema_high"]
        ema_low = analysis["ema_low"]

        if direction == "long":
            farthest_ema = ema_low
            stop_loss = farthest_ema - sl_points
        else:
            farthest_ema = ema_high
            stop_loss = farthest_ema + sl_points

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
