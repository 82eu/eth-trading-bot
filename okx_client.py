"""
OKX API客户端模块 - 支持USDT金额开单
"""
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

    def get_balance(self):
        """获取合约账户余额/权益"""
        try:
            url = f"{self.base_url}/api/v5/account/balance"
            params = {"ccy": "USDT"}
            headers = self._get_auth_headers("GET", "/api/v5/account/balance", "ccy=USDT")
            resp = requests.get(url, params=params, headers=headers, timeout=10)
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

    def _get_auth_headers(self, method, path, body=""):
        """生成OKX API认证头"""
        import hmac
        import hashlib
        import base64
        from datetime import datetime, timezone

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        message = timestamp + method.upper() + path
        if body and method.upper() in ["POST", "PUT"]:
            message += body if isinstance(body, str) else str(body)

        mac = hmac.new(
            bytes(API_SECRET, encoding="utf8"),
            bytes(message, encoding="utf-8"),
            digestmod=hashlib.sha256,
        )
        sign = base64.b64encode(mac.digest()).decode()

        return {
            "OK-ACCESS-KEY": API_KEY,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": PASSPHRASE,
            "Content-Type": "application/json",
        }

    def get_ticker(self, symbol):
        """获取交易对行情"""
        try:
            url = f"{self.base_url}/api/v5/market/ticker"
            params = {"instId": symbol}
            resp = requests.get(url, params=params, timeout=10)
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
        """获取K线数据"""
        try:
            bar_map = {
                "1m": "1m", "5m": "5m", "15m": "15m",
                "30m": "30m", "1h": "1H", "4h": "4H",
                "1d": "1D", "1w": "1W"
            }
            bar = bar_map.get(timeframe, "1H")
            url = f"{self.base_url}/api/v5/market/candles"
            params = {"instId": symbol, "bar": bar, "limit": str(limit)}
            resp = requests.get(url, params=params, timeout=10)
            result = resp.json()
            if result.get("code") == "0":
                candles = result["data"][::-1]
                return candles
            else:
                logger.error(f"获取K线失败: {result}")
                return None
        except Exception as e:
            logger.error(f"获取K线异常: {e}")
            return None

    def usdt_to_size(self, symbol, usdt_amount, price=None):
        """
        将USDT金额转换为合约张数
        :param symbol: 交易对
        :param usdt_amount: USDT金额
        :param price: 当前价格（不传则自动获取）
        :return: 张数（保留小数）
        """
        if price is None:
            ticker = self.get_ticker(symbol)
            if not ticker:
                return None
            price = ticker["last"]

        # U本位合约: 张数 = USDT金额 / 价格 * 杠杆
        # 注意: OKX合约面值通常是1张 = 1币 (ETH合约 1张=0.01ETH)
        # 简化计算: size = usdt_amount / price * leverage
        size = (usdt_amount * LEVERAGE) / price

        # 根据交易对调整小数位
        if "BTC" in symbol:
            size = round(size, 3)
        elif "ETH" in symbol:
            size = round(size, 2)
        else:
            size = round(size, 2)

        logger.info(f"换算: {usdt_amount} USDT @ {price} = {size} 张 (杠杆: {LEVERAGE}x)")
        return size

    def place_order_usdt(self, symbol, side, usdt_amount, order_type="market", price=None, pos_side=None, stop_loss=None, take_profit=None):
        """
        用USDT金额下单
        :param symbol: 交易对
        :param side: buy/sell
        :param usdt_amount: USDT金额（保证金）
        :param order_type: market/limit
        :param price: 限价单价格
        :param pos_side: long/short (默认根据side推断)
        :param stop_loss: 止损价格
        :param take_profit: 止盈价格
        """
        ticker = self.get_ticker(symbol)
        if not ticker:
            logger.error("无法获取行情，无法下单")
            return None

        current_price = ticker["last"]
        size = self.usdt_to_size(symbol, usdt_amount, current_price)

        if size is None or size <= 0:
            logger.error(f"计算张数失败: {size}")
            return None

        # 自动推断持仓方向
        if pos_side is None:
            pos_side = "long" if side == "buy" else "short"

        logger.info(f"下单: {side} {usdt_amount}U = {size}张 @ {current_price} ({pos_side})")
        result = self.place_order(symbol, side, size, order_type, price, pos_side)

        # 下单成功后设置止盈止损
        if result and (stop_loss or take_profit):
            try:
                self.set_stop_take_profit(symbol, pos_side, stop_loss, take_profit, current_price)
            except Exception as e:
                logger.warning(f"设置止盈止损失败: {e}")

        return result

    def place_order(self, symbol, side, size, order_type="market", price=None, pos_side="long"):
        """
        下单
        :param symbol: 交易对
        :param side: buy/sell
        :param size: 数量(张)
        :param order_type: market/limit
        :param price: 限价单价格
        :param pos_side: long/short
        """
        try:
            inst_id = symbol if "-SWAP" in symbol else f"{symbol}-SWAP"
            td_mode = "cross"

            # 设置杠杆
            try:
                self.account.set_leverage(
                    instId=inst_id,
                    lever=str(LEVERAGE),
                    mgnMode="cross",
                    posSide=pos_side
                )
            except Exception as e:
                logger.debug(f"设置杠杆跳过（可能已设置）: {e}")

            if order_type == "market":
                result = self.trade.place_order(
                    instId=inst_id,
                    tdMode=td_mode,
                    side=side,
                    ordType="market",
                    sz=str(size),
                    posSide=pos_side
                )
            else:
                result = self.trade.place_order(
                    instId=inst_id,
                    tdMode=td_mode,
                    side=side,
                    ordType="limit",
                    sz=str(size),
                    px=str(price),
                    posSide=pos_side
                )

            if result.get("code") == "0":
                order_id = result["data"][0]["ordId"]
                logger.info(f"下单成功: {side} {size} {symbol}, 订单ID: {order_id}")
                return order_id
            else:
                logger.error(f"下单失败: {result}")
                return None

        except Exception as e:
            logger.error(f"下单异常: {e}")
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
            inst_id = symbol if "-SWAP" in symbol else f"{symbol}-SWAP"
            result = self.trade.cancel_order(instId=inst_id, ordId=order_id)

            if result.get("code") == "0":
                logger.info(f"撤单成功: {order_id}")
                return True
            else:
                logger.error(f"撤单失败: {result}")
                return False
        except Exception as e:
            logger.error(f"撤单异常: {e}")
            return False

    def get_position(self, symbol):
        """获取持仓信息"""
        try:
            inst_id = symbol if "-SWAP" in symbol else f"{symbol}-SWAP"
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
            inst_id = symbol if "-SWAP" in symbol else f"{symbol}-SWAP"
            result = self.trade.get_order(instId=inst_id, ordId=order_id)

            if result.get("code") == "0":
                return result["data"][0]
            else:
                logger.error(f"查询订单失败: {result}")
                return None
        except Exception as e:
            logger.error(f"查询订单异常: {e}")
            return None

    def set_stop_take_profit(self, symbol, pos_side, stop_loss=None, take_profit=None, current_price=None):
        """
        设置止盈止损（条件单）
        :param symbol: 交易对
        :param pos_side: long/short
        :param stop_loss: 止损价格
        :param take_profit: 止盈价格
        :param current_price: 当前价格（用于计算方向）
        """
        try:
            inst_id = symbol if "-SWAP" in symbol else f"{symbol}-SWAP"

            if stop_loss:
                self._place_algo_order(inst_id, pos_side, "condition", stop_loss, "stop")
                logger.info(f"设置止损: {inst_id} {pos_side} @ {stop_loss}")

            if take_profit:
                self._place_algo_order(inst_id, pos_side, "condition", take_profit, "profit")
                logger.info(f"设置止盈: {inst_id} {pos_side} @ {take_profit}")

            return True
        except Exception as e:
            logger.error(f"设置止盈止损异常: {e}")
            return False

    def _place_algo_order(self, inst_id, pos_side, algo_type, trigger_price, tp_side):
        """
        放置条件单（止盈/止损）
        :param inst_id: 合约ID
        :param pos_side: long/short
        :param algo_type: 条件单类型 condition
        :param trigger_price: 触发价格
        :param tp_side: profit/stop
        """
        import json

        side = "sell" if pos_side == "long" else "buy"

        body = {
            "instId": inst_id,
            "tdMode": "cross",
            "side": side,
            "ordType": "market",
            "sz": "0",
            "posSide": pos_side,
            "algoType": algo_type,
            "triggerPx": str(trigger_price),
        }

        body_str = json.dumps(body, separators=(',', ':'))
        headers = self._get_auth_headers("POST", "/api/v5/trade/order-algo", body_str)
        resp = requests.post(
            f"{self.base_url}/api/v5/trade/order-algo",
            data=body_str,
            headers=headers,
            timeout=10
        )
        result = resp.json()
        if result.get("code") != "0":
            logger.warning(f"条件单设置结果: {result}")
        return result

    def set_leverage(self, symbol, leverage, pos_side="long"):
        """
        设置杠杆
        :param symbol: 交易对
        :param leverage: 杠杆倍数
        :param pos_side: long/short
        """
        try:
            import json
            inst_id = symbol if "-SWAP" in symbol else f"{symbol}-SWAP"
            body = {
                "instId": inst_id,
                "lever": str(leverage),
                "mgnMode": "cross",
                "posSide": pos_side
            }
            body_str = json.dumps(body, separators=(',', ':'))
            headers = self._get_auth_headers("POST", "/api/v5/account/set-leverage", body_str)
            resp = requests.post(
                f"{self.base_url}/api/v5/account/set-leverage",
                data=body_str,
                headers=headers,
                timeout=10
            )
            result = resp.json()
            if result.get("code") == "0":
                logger.info(f"设置杠杆成功: {inst_id} {leverage}x {pos_side}")
                return True
            else:
                logger.warning(f"设置杠杆结果: {result}")
                return False
        except Exception as e:
            logger.warning(f"设置杠杆异常: {e}")
            return False
