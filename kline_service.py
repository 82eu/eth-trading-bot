"""
K线服务 - 多数据源兜底
顺序: OKX -> Binance -> Gate.io合约 -> Gate.io现货
统一输出格式: [ts, o, h, l, c, v] 字符串数组，时间从旧到新
"""
import requests
from loguru import logger


REQUEST_TIMEOUT = 10


class KlineService:
    """多数据源K线服务"""

    SYMBOL_MAP = {
        "ETH-USDT-SWAP": {
            "binance": "ETHUSDT",
            "okx": "ETH-USDT-SWAP",
            "gate": "ETH_USDT",
        },
        "BTC-USDT-SWAP": {
            "binance": "BTCUSDT",
            "okx": "BTC-USDT-SWAP",
            "gate": "BTC_USDT",
        },
    }

    TF_MAP = {
        "1m": {"binance": "1m", "okx": "1m", "gate": "1m"},
        "5m": {"binance": "5m", "okx": "5m", "gate": "5m"},
        "15m": {"binance": "15m", "okx": "15m", "gate": "15m"},
        "30m": {"binance": "30m", "okx": "30m", "gate": "30m"},
        "1h": {"binance": "1h", "okx": "1H", "gate": "1h"},
        "4h": {"binance": "4h", "okx": "4H", "gate": "4h"},
        "1d": {"binance": "1d", "okx": "1D", "gate": "1d"},
    }

    def __init__(self, default_symbol="ETH-USDT-SWAP"):
        self.default_symbol = default_symbol
        self.base_urls = {
            "binance": "https://api.binance.com",
            "okx": "https://www.okx.com",
            "gate": "https://api.gateio.ws",
        }

    def _get_symbol(self, exchange, symbol):
        sym_map = self.SYMBOL_MAP.get(symbol, {})
        return sym_map.get(exchange, symbol)

    def _get_tf(self, exchange, tf):
        tf_map = self.TF_MAP.get(tf, {})
        return tf_map.get(exchange, tf)

    def fetch_klines(self, tf, limit=300, symbol=None):
        """
        获取K线，依次尝试 OKX -> Binance -> Gate.io合约 -> Gate.io现货
        返回 (candles, source_name) 失败返回 (None, None)
        candles 格式: [[ts, o, h, l, c, v], ...] 从旧到新
        """
        if symbol is None:
            symbol = self.default_symbol

        sources = ["okx", "binance", "gate_futures", "gate_spot"]
        for src in sources:
            try:
                if src == "binance":
                    candles = self._fetch_binance(symbol, tf, limit)
                    src_name = "binance"
                elif src == "okx":
                    candles = self._fetch_okx(symbol, tf, limit)
                    src_name = "okx"
                elif src == "gate_futures":
                    candles = self._fetch_gate_futures(symbol, tf, limit)
                    src_name = "gate合约"
                else:
                    candles = self._fetch_gate_spot(symbol, tf, limit)
                    src_name = "gate现货"

                if candles and len(candles) >= 260:
                    logger.debug(f"K线来源: {src_name}, {tf}, {len(candles)}根")
                    return candles, src_name
                else:
                    logger.warning(f"{src_name} K线数据不足: {len(candles) if candles else 0}根 (需要至少260根)")
            except Exception as e:
                logger.warning(f"{src} 获取K线失败: {e}")

        logger.error(f"所有数据源获取K线失败: {tf}")
        return None, None

    def _fetch_binance(self, symbol, tf, limit):
        """Binance 永续合约K线（跟币安APP上的合约图完全一致）"""
        sym = self._get_symbol("binance", symbol)
        interval = self._get_tf("binance", tf)
        url = f"{self.base_urls['binance']}/fapi/v1/klines"

        all_data = []
        remaining = limit
        end_time = None

        while remaining > 0:
            page_limit = min(remaining, 1000)
            params = {
                "symbol": sym,
                "interval": interval,
                "limit": page_limit,
            }
            if end_time:
                params["endTime"] = end_time - 1
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            data = resp.json()
            if not isinstance(data, list) or not data:
                break
            all_data = data + all_data
            if len(data) < page_limit:
                break
            end_time = int(data[0][0])
            remaining -= len(data)
            if len(all_data) >= limit:
                all_data = all_data[-limit:]
                break

        if not all_data:
            return None

        result = []
        for k in all_data:
            result.append([
                str(k[0]),
                str(k[1]),
                str(k[2]),
                str(k[3]),
                str(k[4]),
                str(k[5]),
            ])
        return result

    def _fetch_okx(self, symbol, tf, limit):
        """OKX 合约K线"""
        sym = self._get_symbol("okx", symbol)
        bar = self._get_tf("okx", tf)
        url = f"{self.base_urls['okx']}/api/v5/market/candles"

        all_data = []
        remaining = limit
        before = None

        while remaining > 0:
            page_limit = min(remaining, 100)
            params = {
                "instId": sym,
                "bar": bar,
                "limit": str(page_limit),
            }
            if before:
                params["before"] = before
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            result = resp.json()
            if result.get("code") != "0":
                return None
            data = result.get("data", [])
            if not data:
                break
            all_data.extend(data)
            if len(data) < page_limit:
                break
            before = data[-1][0]
            remaining -= len(data)

        if not all_data:
            return None

        return all_data[::-1]

    def _fetch_gate_futures(self, symbol, tf, limit):
        """Gate.io USDT永续合约K线"""
        sym = self._get_symbol("gate", symbol)
        interval = self._get_tf("gate", tf)
        url = f"{self.base_urls['gate']}/api/v4/futures/usdt/klines"

        all_data = []
        remaining = limit
        to_ts = None

        while remaining > 0:
            page_limit = min(remaining, 1000)
            params = {
                "contract": sym,
                "interval": interval,
                "limit": page_limit,
            }
            if to_ts:
                params["to"] = to_ts - 1
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            data = resp.json()
            if not isinstance(data, list) or not data:
                break
            all_data = data + all_data
            if len(data) < page_limit:
                break
            to_ts = int(data[0][0])
            remaining -= len(data)
            if len(all_data) >= limit:
                all_data = all_data[-limit:]
                break

        if not all_data:
            return None

        result = []
        for k in all_data:
            result.append([
                str(int(float(k[0])) * 1000),
                str(k[5]),
                str(k[3]),
                str(k[4]),
                str(k[2]),
                str(k[1]),
            ])
        return result

    def _fetch_gate_spot(self, symbol, tf, limit):
        """Gate.io 现货K线（最后兜底）"""
        sym = self._get_symbol("gate", symbol)
        interval = self._get_tf("gate", tf)
        url = f"{self.base_urls['gate']}/api/v4/spot/candlesticks"

        all_data = []
        remaining = limit
        to_ts = None

        while remaining > 0:
            page_limit = min(remaining, 1000)
            params = {
                "currency_pair": sym,
                "interval": interval,
                "limit": page_limit,
            }
            if to_ts:
                params["to"] = to_ts - 1
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            data = resp.json()
            if not isinstance(data, list) or not data:
                break
            all_data = data + all_data
            if len(data) < page_limit:
                break
            to_ts = int(data[0][0])
            remaining -= len(data)
            if len(all_data) >= limit:
                all_data = all_data[-limit:]
                break

        if not all_data:
            return None

        result = []
        for k in all_data:
            result.append([
                str(int(float(k[0])) * 1000),
                str(k[5]),
                str(k[3]),
                str(k[4]),
                str(k[2]),
                str(k[1]),
            ])
        return result


_kline_service_instances = {}


def get_kline_service(symbol=None):
    if symbol is None:
        from config import DEFAULT_SYMBOL
        symbol = DEFAULT_SYMBOL
    global _kline_service_instances
    if symbol not in _kline_service_instances:
        _kline_service_instances[symbol] = KlineService(symbol)
    return _kline_service_instances[symbol]
