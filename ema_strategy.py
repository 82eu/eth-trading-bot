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
        """计算EMA（返回最新值）"""
        if len(prices) < period:
            return None
        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period
        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def _ema_full_history(self, prices, period):
        """计算EMA完整历史（增量O(n)，结果与逐次计算一致）"""
        if len(prices) < period:
            return []
        k = 2.0 / (period + 1)
        result = []
        prev = sum(prices[:period]) / period
        result.append(prev)
        for i in range(period, len(prices)):
            cur = prices[i] * k + prev * (1 - k)
            result.append(cur)
            prev = cur
        return result

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

        ema180_full = self._ema_full_history(closes, self.ema_fast)
        ema250_full = self._ema_full_history(closes, self.ema_slow)

        crossing_count = 0
        min_cross_distance = float('inf')
        last_cross_idx = -1
        n = min(len(ema180_full), len(ema250_full))
        for i in range(1, n):
            prev_diff = ema180_full[i-1] - ema250_full[i-1]
            curr_diff = ema180_full[i] - ema250_full[i]
            if prev_diff * curr_diff < 0:
                crossing_count += 1
                if last_cross_idx >= 0:
                    distance = i - last_cross_idx
                    if distance < min_cross_distance:
                        min_cross_distance = distance
                last_cross_idx = i

        lookback = min(100, n)
        recent_crosses = 0
        if n >= lookback:
            for i in range(n - lookback, n - 1):
                prev_diff = ema180_full[i] - ema250_full[i]
                curr_diff = ema180_full[i+1] - ema250_full[i+1]
                if prev_diff * curr_diff < 0:
                    recent_crosses += 1

        is_entangled = False
        if crossing_count >= 3 and min_cross_distance <= 30:
            is_entangled = True
        elif recent_crosses >= 3:
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
        获取趋势方向：
        1. 价格在EMA区间上方 → 趋势=long（回踩做多）
        2. 价格在EMA区间下方 → 趋势=short（反弹做空）
        3. 价格在区间内且线纠缠 → 递推到大周期
        4. 价格在区间内但线不纠缠 → 用当前周期EMA排列方向
        :param analysis_map: {tf: analysis_result}
        :param timeframe: 起始周期
        :return: 'long' / 'short' / 'neutral'
        """
        tf_order = self.TIMEFRAMES
        if timeframe not in tf_order:
            return "neutral"
        idx = tf_order.index(timeframe)

        current_tf = tf_order[idx]
        if current_tf not in analysis_map or not analysis_map[current_tf]:
            return "neutral"

        a = analysis_map[current_tf]
        price = a["current_price"]
        ema_high = a["ema_high"]
        ema_low = a["ema_low"]

        if price > ema_high:
            return "long"
        elif price < ema_low:
            return "short"
        else:
            for i in range(idx, len(tf_order)):
                tf = tf_order[i]
                if tf in analysis_map and analysis_map[tf]:
                    ai = analysis_map[tf]
                    if not ai.get("is_entangled", False):
                        return ai["trend"]

            return a["trend"]

    def get_entry_side(self, analysis, direction):
        """
        固定入场方向：
        - 做多：回踩EMA180（快线），从上方跌下来在EMA180处接多
        - 做空：反弹EMA180（快线），从下方涨上去在EMA180处接空
        止损放在EMA250（慢线）外侧
        :param analysis: 单周期分析结果
        :param direction: 'long' / 'short'
        :return: (entry_ema, exit_ema) 入口EMA线、出口EMA线
        """
        ema180 = analysis["ema180"]
        ema250 = analysis["ema250"]

        if direction == "long":
            return ema180, ema250
        else:
            return ema180, ema250

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
            entries.append({"index": 1, "price": entry_line, "description": "入口EMA"})
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
