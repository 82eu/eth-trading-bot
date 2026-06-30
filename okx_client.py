"""
OKX API客户端模块 - 支持USDT金额开单
"""
import json
import hmac
import hashlib
import base64
from datetime import datetime, timezone
from okx import AccountClient, TradingClient
from loguru import logger
from config import API_KEY, API_SECRET, PASSPHRASE, SIMULATE, LEVERAGE
import requests


class OKXClient:
    """OKX交易所客户端"""

    def __init__(self):
        self.base_url = "https://www.okx.com"
        self.account = AccountClient(
            apikey=API_KEY,
            apisecret=API_SECRET,
            passphrase=PASSPHRASE,
            simulation=SIMULATE
        )
        self.trade = TradingClient(
            apikey=API_KEY,
            apisecret=API_SECRET,
            passphrase=PASSPHRASE,
            simulation=SIMULATE
        )
        self.simulate = SIMULATE

    def _get_auth_headers(self, method, path, body=""):
        """
        生成OKX API认证头
        GET请求: body 是 query string (如 "ccy=USDT")，会拼到 path 后面
        POST请求: body 是 JSON 字符串
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        method_upper = method.upper()
        if method_upper == "GET" and body:
            sign_path = path + "?" + body
        else:
            sign_path = path

        message = timestamp + method_upper + sign_path
        if body and method_upper in ["POST", "PUT", "DELETE"]:
            message += body if isinstance(body, str) else str(body)

        mac = hmac.new(
            bytes(API_SECRET, encoding="utf8"),
            bytes(message, encoding="utf-8"),
            digestmod=hashlib.sha256,
        )
        sign = base64.b64encode(mac.digest()).decode()

        headers = {
            "OK-ACCESS-KEY": API_KEY,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": PASSPHRASE,
            "Content-Type": "application/json",
        }
        if self.simulate:
            headers["x-simulated-trading"] = "1"
        return headers

    @staticmethod
    def _normalize_symbol(symbol):
        return symbol if "-SWAP" in symbol else f"{symbol}-SWAP"

    def get_balance(self):
        """获取合约账户余额/权益"""
        try:
            query = "ccy=USDT"
            url = f"{self.base_url}/api/v5/account/balance?{query}"
            headers = self._get_auth_headers("GET", "/api/v5/account/balance", query)
            resp = requests.get(url, headers=headers, timeout=10)
            result = resp.json()
            if result.get("code") == "0":
                data = result.get("data", [])
                for item in data:
                    details = item.get("details", [])
                    for d in details:
                        if d.get("ccy") == "USDT":
                            logger.info(f"合约账户: USDT 权益: {d.get('eq')} 可用: {d.get('availEq')}")
                return data
            else:
                logger.error(f"获取余额失败: {result}")
                return None
        except Exception as e:
            logger.error(f"获取余额异常: {e}")
            return None

    def get_ticker(self, symbol):
        """获取交易对行情"""
        try:
            params = {"instId": self._normalize_symbol(symbol)}
            resp = requests.get(f"{self.base_url}/api/v5/market/ticker", params=params, timeout=10)
            result = resp.json()
            if result.get("code") == "0":
                data = result["data"][0]
                return {
                    "last": float(data["last"]),
                    "buy": float(data["bidPx"]),
                    "sell": float(data["askPx"]),
                    "high": float(data["high24h"]),
                    "low": float(data["low24h"]),
                    "volume": float(data["vol24h"])
                }
            else:
                logger.error(f"获取行情失败: {result}")
                return None
        except Exception as e:
            logger.error(f"获取行情异常: {e}")
            return None

    def get_candles(self, symbol, timeframe, limit=100):
        """获取K线数据（公共接口，优先用kline_service多数据源，这里保留兼容）"""
        try:
            bar_map = {
                "1m": "1m", "5m": "5m", "15m": "15m",
                "30m": "30m", "1h": "1H", "4h": "4H",
                "1d": "1D", "1w": "1W"
            }
            bar = bar_map.get(timeframe, "1H")
            inst_id = self._normalize_symbol(symbol)

            all_data = []
            remaining = limit
            before = None

            while remaining > 0:
                page_limit = min(remaining, 100)
                params = {"instId": inst_id, "bar": bar, "limit": str(page_limit)}
                if before:
                    params["before"] = before
                resp = requests.get(f"{self.base_url}/api/v5/market/candles", params=params, timeout=10)
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

            return all_data[::-1] if all_data else None
        except Exception as e:
            logger.error(f"获取K线异常: {e}")
            return None

    def set_leverage(self, symbol, leverage, pos_side="long"):
        """设置杠杆（HTTP直连）"""
        try:
            inst_id = self._normalize_symbol(symbol)
            body = json.dumps({
                "instId": inst_id,
                "lever": str(leverage),
                "mgnMode": "cross",
                "posSide": pos_side
            }, separators=(',', ':'))
            headers = self._get_auth_headers("POST", "/api/v5/account/set-leverage", body)
            resp = requests.post(
                f"{self.base_url}/api/v5/account/set-leverage",
                data=body, headers=headers, timeout=10
            )
            result = resp.json()
            ok = result.get("code") == "0"
            if ok:
                logger.info(f"设置杠杆成功: {inst_id} {leverage}x {pos_side}")
            else:
                logger.warning(f"设置杠杆结果: {result}")
            return ok
        except Exception as e:
            logger.warning(f"设置杠杆异常: {e}")
            return False

    def usdt_to_size(self, symbol, usdt_amount, price=None, leverage=None):
        """USDT金额转合约张数
        usdt_amount: 合约价值（用户输入的金额就是要开的仓位大小）
        OKX合约面值: ETH=0.1ETH/张, BTC=0.01BTC/张
        """
        if price is None:
            ticker = self.get_ticker(symbol)
            if not ticker:
                return None
            price = ticker["last"]

        lev = leverage if leverage else LEVERAGE
        eth_amount = usdt_amount / price

        if "BTC" in symbol:
            contract_size = 0.01
            size = round(eth_amount / contract_size, 3)
        elif "ETH" in symbol:
            contract_size = 0.1
            size = round(eth_amount / contract_size, 2)
        else:
            contract_size = 0.1
            size = round(eth_amount / contract_size, 2)

        margin = usdt_amount / lev
        logger.info(f"换算: {usdt_amount} USDT合约价值 = {eth_amount:.6f} ETH = {size} 张, 保证金: {margin:.2f}U (杠杆: {lev}x, 面值: {contract_size})")
        return size

    def place_order_usdt(self, symbol, side, usdt_amount, order_type="market",
                         price=None, pos_side=None, stop_loss=None, take_profit=None, leverage=None):
        """用USDT金额下单，自动带上止盈止损"""
        ticker = self.get_ticker(symbol)
        if not ticker:
            logger.error("无法获取行情，无法下单")
            self.last_error = "无法获取行情"
            return None

        current_price = ticker["last"]
        lev = leverage if leverage else LEVERAGE
        size = self.usdt_to_size(symbol, usdt_amount, current_price, lev)

        if size is None or size <= 0:
            logger.error(f"计算张数失败: {size}")
            self.last_error = f"计算张数失败: {size}"
            return None

        balance = self.get_balance()
        if balance:
            avail_eq = 0
            for item in balance:
                for d in item.get("details", []):
                    if d.get("ccy") == "USDT":
                        avail_eq = float(d.get("availEq", "0"))
                        break
            margin_needed = usdt_amount / lev
            if avail_eq < margin_needed:
                msg = f"余额不足: 可用 {avail_eq:.2f} USDT, 需要保证金 {margin_needed:.2f} USDT ({usdt_amount:.2f}U合约价值 × {lev}x杠杆)"
                logger.error(msg)
                self.last_error = msg
                return None

        if pos_side is None:
            pos_side = "long" if side == "buy" else "short"

        logger.info(f"下单: {side} {usdt_amount}U = {size}张 @ {current_price} ({pos_side}, {lev}x)")
        order_id = self.place_order(symbol, side, size, order_type, price, pos_side, lev,
                                    stop_loss=stop_loss, take_profit=take_profit)

        return order_id

    def place_order(self, symbol, side, size, order_type="market", price=None, pos_side="long", leverage=None,
                    stop_loss=None, take_profit=None):
        """下单（HTTP直连，支持止盈止损）"""
        try:
            inst_id = self._normalize_symbol(symbol)
            lev = leverage if leverage else LEVERAGE

            try:
                self.set_leverage(symbol, lev, pos_side)
            except Exception as e:
                logger.debug(f"设置杠杆跳过: {e}")

            body_dict = {
                "instId": inst_id,
                "tdMode": "cross",
                "side": side,
                "ordType": order_type,
                "sz": str(size),
                "posSide": pos_side,
            }
            if order_type == "limit" and price:
                body_dict["px"] = str(price)

            body = json.dumps(body_dict, separators=(',', ':'))
            headers = self._get_auth_headers("POST", "/api/v5/trade/order", body)
            resp = requests.post(
                f"{self.base_url}/api/v5/trade/order",
                data=body, headers=headers, timeout=10
            )
            result = resp.json()

            if result.get("code") == "0":
                order_id = result["data"][0]["ordId"]
                logger.info(f"下单成功: {side} {size} {symbol}, 订单ID: {order_id}")

                if stop_loss or take_profit:
                    import time as _time
                    _time.sleep(0.8)
                    try:
                        self.set_stop_take_profit(symbol, pos_side, stop_loss, take_profit)
                    except Exception as e:
                        logger.warning(f"设置止盈止损失败: {e}")

                return order_id
            else:
                logger.error(f"下单失败: {result}")
                self.last_error = f"下单失败: {result.get('msg', '未知错误')} (code: {result.get('code', '?')})"
                return None
        except Exception as e:
            logger.error(f"下单异常: {e}")
            self.last_error = f"下单异常: {e}"
            return None

    def close_position_usdt(self, symbol, side, usdt_amount, pos_side=None):
        """用USDT金额平仓"""
        ticker = self.get_ticker(symbol)
        if not ticker:
            return None
        size = self.usdt_to_size(symbol, usdt_amount, ticker["last"])
        if size is None or size <= 0:
            return None
        if pos_side is None:
            pos_side = "short" if side == "buy" else "long"
        return self.place_order(symbol, side, size, "market", None, pos_side)

    def cancel_order(self, symbol, order_id):
        """撤单"""
        try:
            inst_id = self._normalize_symbol(symbol)
            result = self.trade.cancel_order(instId=inst_id, ordId=order_id)
            ok = result.get("code") == "0"
            if ok:
                logger.info(f"撤单成功: {order_id}")
            else:
                logger.error(f"撤单失败: {result}")
            return ok
        except Exception as e:
            logger.error(f"撤单异常: {e}")
            return False

    def get_position(self, symbol):
        """获取持仓信息"""
        try:
            inst_id = self._normalize_symbol(symbol)
            result = self.account.get_positions(instId=inst_id)
            if result.get("code") == "0":
                positions = result["data"]
                for pos in positions:
                    if float(pos.get("pos", 0)) != 0:
                        logger.info(f"持仓: {pos.get('instId')} {pos.get('posSide')} {pos.get('pos')}张 @ {pos.get('avgPx')}")
                return positions
            else:
                logger.error(f"获取持仓失败: {result}")
                return None
        except Exception as e:
            logger.error(f"获取持仓异常: {e}")
            return None

    def get_order(self, symbol, order_id):
        """查询订单状态"""
        try:
            inst_id = self._normalize_symbol(symbol)
            result = self.trade.get_order(instId=inst_id, ordId=order_id)
            if result.get("code") == "0":
                return result["data"][0]
            else:
                logger.error(f"查询订单失败: {result}")
                return None
        except Exception as e:
            logger.error(f"查询订单异常: {e}")
            return None

    def set_stop_take_profit(self, symbol, pos_side, stop_loss=None, take_profit=None, size=None):
        """设置止盈止损条件单"""
        try:
            inst_id = self._normalize_symbol(symbol)
            if stop_loss:
                self._place_algo_order(inst_id, pos_side, "stop", stop_loss, size)
                logger.info(f"设置止损: {inst_id} {pos_side} @ {stop_loss}")
            if take_profit:
                self._place_algo_order(inst_id, pos_side, "profit", take_profit, size)
                logger.info(f"设置止盈: {inst_id} {pos_side} @ {take_profit}")
            return True
        except Exception as e:
            logger.error(f"设置止盈止损异常: {e}")
            return False

    def _place_algo_order(self, inst_id, pos_side, ord_type, trigger_price, size=None):
        """放置条件单（止盈/止损）- 使用SDK"""
        side = "sell" if pos_side == "long" else "buy"
        try:
            params = dict(
                instId=inst_id,
                tdMode="cross",
                side=side,
                ordType=ord_type,
                posSide=pos_side,
                triggerPx=str(trigger_price),
                triggerType="1",
                ordPx="-1",
            )
            if size:
                params["sz"] = str(size)
            else:
                params["closeFraction"] = "1"

            result = self.trade.place_order_algo(**params)
            if result.get("code") != "0":
                logger.warning(f"条件单设置失败[{ord_type}]: {result}")
            else:
                logger.info(f"条件单设置成功[{ord_type}]: {trigger_price}")
            return result
        except Exception as e:
            logger.error(f"条件单设置异常[{ord_type}]: {e}")
            return {"code": "-1", "msg": str(e)}
