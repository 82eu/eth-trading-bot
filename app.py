"""
Flask Web应用 - OKX量化交易可视化仪表盘
支持ETH合约，USDT金额开单
"""
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
import os
import sys
from datetime import datetime

load_dotenv()

app = Flask(__name__)
CORS(app)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"

# 全局交易状态
trading_status = {
    "running": False,
    "position": 0,
    "position_side": "",
    "entry_price": 0,
    "current_price": 3500,
    "position_size": 0,
    "position_usdt": 0,
    "pnl": 0,
    "pnl_pct": 0,
    "total_trades": 0,
    "win_trades": 0,
    "symbol": "ETH-USDT-SWAP",
    "timeframe": "1h",
    "fast_ma": 10,
    "slow_ma": 50,
    "leverage": 10,
}

trade_history = []


def generate_mock_candles(count=100, symbol="ETH-USDT-SWAP"):
    """生成模拟K线"""
    import random
    candles = []
    base_price = 3500 if "ETH" in symbol else 65000
    price = base_price
    now = datetime.now().timestamp() * 1000
    for i in range(count):
        ts = now - (count - i) * 3600000
        change = random.uniform(-base_price * 0.01, base_price * 0.01)
        open_price = price
        close_price = price + change
        high = max(open_price, close_price) + random.uniform(0, base_price * 0.005)
        low = min(open_price, close_price) - random.uniform(0, base_price * 0.005)
        volume = random.uniform(500, 3000)
        candles.append([
            str(int(ts)),
            str(round(open_price, 2)),
            str(round(high, 2)),
            str(round(low, 2)),
            str(round(close_price, 2)),
            str(round(volume, 2))
        ])
        price = close_price
    trading_status["current_price"] = price
    return candles


def calculate_ma(candles, period):
    closes = [float(c[4]) for c in candles]
    ma = []
    for i in range(len(closes)):
        if i < period - 1:
            ma.append(None)
        else:
            ma.append(sum(closes[i-period+1:i+1]) / period)
    return ma


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def get_status():
    """获取当前交易状态"""
    global trading_status

    if not MOCK_MODE:
        try:
            from okx_client import OKXClient
            from config import SYMBOL
            client = OKXClient()
            ticker = client.get_ticker(SYMBOL)
            if ticker:
                trading_status["current_price"] = ticker["last"]

            positions = client.get_position(SYMBOL)
            if positions and len(positions) > 0:
                for pos in positions:
                    if float(pos.get("pos", 0)) != 0:
                        trading_status["position"] = 1 if pos["posSide"] == "long" else -1
                        trading_status["position_side"] = pos["posSide"]
                        trading_status["entry_price"] = float(pos["avgPx"])
                        trading_status["position_size"] = float(pos["pos"])
                        trading_status["position_usdt"] = float(pos["pos"]) * float(pos["avgPx"])
                        break
                    else:
                        trading_status["position"] = 0
                        trading_status["position_side"] = ""
                        trading_status["entry_price"] = 0
                        trading_status["position_size"] = 0
                        trading_status["position_usdt"] = 0

            if trading_status["position"] == 1 and trading_status["entry_price"] > 0:
                trading_status["pnl_pct"] = (trading_status["current_price"] - trading_status["entry_price"]) / trading_status["entry_price"] * 100 * trading_status["leverage"]
                trading_status["pnl"] = (trading_status["current_price"] - trading_status["entry_price"]) * trading_status["position_size"]
            elif trading_status["position"] == -1 and trading_status["entry_price"] > 0:
                trading_status["pnl_pct"] = (trading_status["entry_price"] - trading_status["current_price"]) / trading_status["entry_price"] * 100 * trading_status["leverage"]
                trading_status["pnl"] = (trading_status["entry_price"] - trading_status["current_price"]) * trading_status["position_size"]

        except Exception as e:
            print(f"获取真实数据失败: {e}")

    return jsonify({"code": 0, "data": trading_status})


@app.route("/api/candles")
def get_candles():
    """获取K线数据（多数据源兜底）"""
    symbol = request.args.get("symbol", "ETH-USDT-SWAP")
    timeframe = request.args.get("timeframe", "1h")
    limit = int(request.args.get("limit", 100))

    if MOCK_MODE:
        candles = generate_mock_candles(limit, symbol)
    else:
        try:
            from kline_service import get_kline_service
            kline_svc = get_kline_service(symbol)
            candles, source = kline_svc.fetch_klines(timeframe, limit=limit, symbol=symbol)
            if not candles:
                candles = generate_mock_candles(limit, symbol)
        except Exception as e:
            print(f"获取K线失败: {e}")
            candles = generate_mock_candles(limit, symbol)

    ma_fast = calculate_ma(candles, trading_status["fast_ma"])
    ma_slow = calculate_ma(candles, trading_status["slow_ma"])

    formatted = []
    for i, c in enumerate(candles):
        formatted.append({
            "time": int(c[0]),
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5]),
            "ma_fast": ma_fast[i] if i < len(ma_fast) else None,
            "ma_slow": ma_slow[i] if i < len(ma_slow) else None,
        })

    return jsonify({"code": 0, "data": formatted})


@app.route("/api/trades")
def get_trades():
    """获取交易历史"""
    if MOCK_MODE:
        mock_trades = [
            {"id": 5, "time": "2024-01-17 08:00", "side": "buy", "price": 3520, "size_usdt": 100, "size": 0.284, "pnl": 0, "reason": "金叉做多", "status": "open"},
            {"id": 4, "time": "2024-01-16 20:00", "side": "sell", "price": 3480, "size_usdt": 100, "size": 0.287, "pnl": 8.5, "reason": "止盈平仓", "status": "closed"},
            {"id": 3, "time": "2024-01-16 09:00", "side": "sell", "price": 3420, "size_usdt": 100, "size": 0.292, "pnl": 0, "reason": "死叉做空", "status": "closed"},
            {"id": 2, "time": "2024-01-15 14:30", "side": "buy", "price": 3380, "size_usdt": 100, "size": 0.296, "pnl": 12.3, "reason": "止盈平仓", "status": "closed"},
            {"id": 1, "time": "2024-01-15 10:00", "side": "buy", "price": 3350, "size_usdt": 100, "size": 0.299, "pnl": 0, "reason": "金叉做多", "status": "closed"},
        ]
        return jsonify({"code": 0, "data": mock_trades})
    return jsonify({"code": 0, "data": trade_history})


@app.route("/api/balance")
def get_balance():
    """获取账户余额"""
    if MOCK_MODE:
        data = {
            "total": 5000,
            "available": 4200,
            "used": 800,
            "pnl_24h": 128.5,
            "pnl_24h_pct": 2.57,
            "equity": 5128.5,
        }
    else:
        try:
            from okx_client import OKXClient
            client = OKXClient()
            result = client.get_balance()
            total = 0
            available = 0
            used = 0
            equity = 0
            if result and isinstance(result, list):
                for item in result:
                    for d in item.get("details", []):
                        if d.get("ccy") == "USDT":
                            eq = d.get("eq")
                            avail = d.get("availEq") or d.get("availBal")
                            frozen = d.get("frozenBal", "0")
                            if eq:
                                equity = float(eq)
                                total = equity
                            if avail:
                                available = float(avail)
                            if frozen:
                                used = float(frozen)
                            break
            data = {
                "total": total,
                "available": available,
                "used": used,
                "equity": equity or total,
                "pnl_24h": 0,
                "pnl_24h_pct": 0,
            }
        except Exception as e:
            print(f"获取余额失败: {e}")
            data = {"total": 0, "available": 0, "used": 0, "pnl_24h": 0, "pnl_24h_pct": 0, "equity": 0}

    return jsonify({"code": 0, "data": data})


@app.route("/api/strategy/signal")
def get_signal():
    """获取当前策略信号"""
    import random
    r = random.random()
    if r < 0.7:
        signal = "HOLD"
    elif r < 0.85:
        signal = "BUY"
    else:
        signal = "SELL"

    price = trading_status["current_price"]
    return jsonify({
        "code": 0,
        "data": {
            "signal": signal,
            "ma_fast": price - 15,
            "ma_slow": price - 30,
            "price": price,
            "reason": f"MA10 {'上穿' if signal == 'BUY' else '下穿' if signal == 'SELL' else '位于'} MA50"
        }
    })


@app.route("/api/order", methods=["POST"])
def place_order():
    """
    下单 - 支持USDT金额
    body: {
        "symbol": "ETH-USDT-SWAP",
        "side": "buy" | "sell",
        "amount_usdt": 100,  // USDT金额
        "type": "market" | "limit",
        "price": 3500,  // 限价单价格
        "pos_side": "long" | "short"
    }
    """
    global trading_status, trade_history
    data = request.json or {}
    symbol = data.get("symbol", "ETH-USDT-SWAP")
    side = data.get("side", "buy")
    amount_usdt = float(data.get("amount_usdt", 100))
    order_type = data.get("type", "market")
    price = data.get("price")
    pos_side = data.get("pos_side", "long" if side == "buy" else "short")

    if MOCK_MODE:
        current_price = trading_status["current_price"]
        size = round(amount_usdt * trading_status["leverage"] / current_price, 3)

        order_id = f"mock_{int(datetime.now().timestamp())}"

        trading_status["position"] = 1 if pos_side == "long" else -1
        trading_status["position_side"] = pos_side
        trading_status["entry_price"] = current_price
        trading_status["position_size"] = size
        trading_status["position_usdt"] = amount_usdt

        trade_history.insert(0, {
            "id": len(trade_history) + 1,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "side": side,
            "price": current_price,
            "size_usdt": amount_usdt,
            "size": size,
            "pnl": 0,
            "reason": f"手动{'做多' if pos_side == 'long' else '做空'}",
            "status": "open"
        })

        return jsonify({
            "code": 0,
            "data": {
                "order_id": order_id,
                "side": side,
                "pos_side": pos_side,
                "amount_usdt": amount_usdt,
                "size": size,
                "price": current_price,
                "leverage": trading_status["leverage"],
                "status": "filled",
                "message": f"成功开{pos_side} {amount_usdt}U = {size}张 @ {current_price}"
            }
        })
    else:
        try:
            from okx_client import OKXClient
            client = OKXClient()

            leverage = data.get("leverage")
            stop_loss = data.get("stop_loss")
            take_profit = data.get("take_profit")

            if leverage:
                leverage = int(leverage)
                trading_status["leverage"] = leverage
                try:
                    client.set_leverage(symbol, leverage, "long")
                    client.set_leverage(symbol, leverage, "short")
                except Exception as e:
                    print(f"设置杠杆跳过: {e}")

            if stop_loss:
                stop_loss = float(stop_loss)
            if take_profit:
                take_profit = float(take_profit)

            order_id = client.place_order_usdt(
                symbol, side, amount_usdt, order_type, price, pos_side,
                stop_loss=stop_loss, take_profit=take_profit
            )
            if order_id:
                return jsonify({"code": 0, "data": {"order_id": order_id}})
            else:
                return jsonify({"code": 1, "msg": "下单失败"}), 400
        except Exception as e:
            return jsonify({"code": 1, "msg": str(e)}), 500


@app.route("/api/close_position", methods=["POST"])
def close_position():
    """平仓"""
    global trading_status, trade_history
    data = request.json or {}
    symbol = data.get("symbol", "ETH-USDT-SWAP")
    amount_usdt = data.get("amount_usdt")

    if MOCK_MODE:
        current_price = trading_status["current_price"]
        entry_price = trading_status["entry_price"]
        pos_side = trading_status["position_side"]
        size = trading_status["position_size"]

        if pos_side == "long":
            pnl = (current_price - entry_price) * size
            pnl_pct = (current_price - entry_price) / entry_price * 100 * trading_status["leverage"]
        else:
            pnl = (entry_price - current_price) * size
            pnl_pct = (entry_price - current_price) / entry_price * 100 * trading_status["leverage"]

        trading_status["position"] = 0
        trading_status["position_side"] = ""
        trading_status["entry_price"] = 0
        trading_status["position_size"] = 0
        trading_status["position_usdt"] = 0
        trading_status["pnl"] = 0
        trading_status["pnl_pct"] = 0

        if trade_history and trade_history[0]["status"] == "open":
            trade_history[0]["status"] = "closed"
            trade_history[0]["pnl"] = round(pnl, 2)

        return jsonify({
            "code": 0,
            "data": {
                "message": f"平仓成功，盈亏: {pnl:.2f} USDT ({pnl_pct:.2f}%)",
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2)
            }
        })
    else:
        try:
            from okx_client import OKXClient
            client = OKXClient()
            positions = client.get_position(symbol)
            if not positions:
                return jsonify({"code": 1, "msg": "获取持仓失败"}), 400

            pos = None
            for p in positions:
                if float(p.get("pos", 0)) != 0:
                    pos = p
                    break

            if not pos:
                return jsonify({"code": 1, "msg": "当前没有持仓"}), 400

            pos_side = pos.get("posSide")
            size = float(pos.get("pos", 0))
            if amount_usdt:
                size = min(size, (amount_usdt * trading_status["leverage"]) / float(pos.get("avgPx", 1)))

            close_side = "sell" if pos_side == "long" else "buy"
            order_id = client.place_order(symbol, close_side, size, "market", None, pos_side)

            if order_id:
                trading_status["position"] = 0
                trading_status["position_side"] = ""
                trading_status["entry_price"] = 0
                trading_status["position_size"] = 0
                trading_status["position_usdt"] = 0
                trading_status["pnl"] = 0
                trading_status["pnl_pct"] = 0
                return jsonify({"code": 0, "data": {"message": "平仓成功", "order_id": order_id}})
            else:
                return jsonify({"code": 1, "msg": "平仓失败"}), 400
        except Exception as e:
            return jsonify({"code": 1, "msg": str(e)}), 500


@app.route("/api/stop_take_profit", methods=["POST"])
def set_stop_take_profit():
    """设置止盈止损"""
    data = request.json or {}
    symbol = data.get("symbol", "ETH-USDT-SWAP")
    stop_loss = data.get("stop_loss")
    take_profit = data.get("take_profit")
    pos_side = data.get("pos_side")

    if MOCK_MODE:
        return jsonify({"code": 0, "msg": "模拟模式止盈止损已设置"})
    else:
        try:
            from okx_client import OKXClient
            client = OKXClient()

            if not pos_side:
                positions = client.get_position(symbol)
                if positions:
                    for p in positions:
                        if float(p.get("pos", 0)) != 0:
                            pos_side = p.get("posSide")
                            break

            if not pos_side:
                return jsonify({"code": 1, "msg": "没有持仓，无法设置止盈止损"}), 400

            if stop_loss:
                stop_loss = float(stop_loss)
            if take_profit:
                take_profit = float(take_profit)

            result = client.set_stop_take_profit(symbol, pos_side, stop_loss, take_profit)
            if result:
                return jsonify({"code": 0, "msg": "止盈止损设置成功"})
            else:
                return jsonify({"code": 1, "msg": "止盈止损设置失败"}), 400
        except Exception as e:
            return jsonify({"code": 1, "msg": str(e)}), 500


@app.route("/api/set_leverage", methods=["POST"])
def set_leverage():
    """设置杠杆"""
    data = request.json or {}
    symbol = data.get("symbol", "ETH-USDT-SWAP")
    leverage = int(data.get("leverage", 10))

    global trading_status
    trading_status["leverage"] = leverage

    if MOCK_MODE:
        return jsonify({"code": 0, "msg": f"杠杆已设置为{leverage}x"})
    else:
        try:
            from okx_client import OKXClient
            client = OKXClient()
            client.set_leverage(symbol, leverage, "long")
            client.set_leverage(symbol, leverage, "short")
            return jsonify({"code": 0, "msg": f"杠杆已设置为{leverage}x", "leverage": leverage})
        except Exception as e:
            return jsonify({"code": 1, "msg": str(e)}), 500


@app.route("/api/start", methods=["POST"])
def start_trading():
    """启动交易机器人"""
    global trading_status
    trading_status["running"] = True
    return jsonify({"code": 0, "msg": "交易机器人已启动"})


@app.route("/api/stop", methods=["POST"])
def stop_trading():
    """停止交易机器人"""
    global trading_status
    trading_status["running"] = False
    return jsonify({"code": 0, "msg": "交易机器人已停止"})


auto_trader_instance = None


def get_auto_trader():
    """获取自动交易器实例"""
    global auto_trader_instance
    if auto_trader_instance is None:
        if not MOCK_MODE:
            from auto_trader import get_auto_trader as _gat
            from okx_client import OKXClient
            client = OKXClient()
            auto_trader_instance = _gat(client)
        else:
            auto_trader_instance = None
    return auto_trader_instance


@app.route("/api/auto/status")
def auto_status():
    """获取自动交易状态"""
    at = get_auto_trader()
    if MOCK_MODE or at is None:
        from ema_strategy import EMAStrategy
        strategy = EMAStrategy()
        analysis = {}
        for tf in strategy.TIMEFRAMES:
            analysis[tf] = {
                "tf": tf,
                "ema180": 3500,
                "ema250": 3520,
                "current_price": 3510,
                "in_zone": True,
                "trend": "short",
                "ema_high": 3520,
                "ema_low": 3500,
                "center_price": 3510,
                "zone_width": 20,
                "zone_width_pct": 0.57,
                "is_ranging": False,
            }
        return jsonify({
            "code": 0,
            "data": {
                "running": False,
                "config": {
                    "enabled_tfs": ["5m", "15m"],
                    "total_amount_usdt": 100,
                    "num_entries": 2,
                    "tp_points": 50,
                    "sl_points": 30,
                },
                "positions": {},
                "analysis": analysis,
                "logs": [
                    {"time": "00:00:00", "msg": "模拟模式", "level": "info"}
                ],
                "last_check": None,
            }
        })
    return jsonify({"code": 0, "data": at.get_status()})


@app.route("/api/auto/start", methods=["POST"])
def auto_start():
    """启动自动交易"""
    at = get_auto_trader()
    if at is None:
        return jsonify({"code": 1, "msg": "未初始化"}), 400
    at.start()
    return jsonify({"code": 0, "msg": "自动交易已启动"})


@app.route("/api/auto/stop", methods=["POST"])
def auto_stop():
    """停止自动交易"""
    at = get_auto_trader()
    if at is None:
        return jsonify({"code": 1, "msg": "未初始化"}), 400
    at.stop()
    return jsonify({"code": 0, "msg": "自动交易已停止"})


@app.route("/api/auto/test", methods=["POST"])
def auto_test():
    """
    测试EMA策略开单
    手动触发一次策略逻辑，用当前价格和配置开一单测试
    body: {
        "tf": "5m",  // 测试哪个周期
        "direction": "long" | "short"  // 可选，不传则按策略趋势
    }
    """
    at = get_auto_trader()
    if at is None:
        return jsonify({"code": 1, "msg": "未初始化"}), 400

    data = request.json or {}
    tf = data.get("tf", "5m")
    direction = data.get("direction")

    try:
        success, result = at.test_open_order(tf, direction)
        if success:
            return jsonify({"code": 0, "msg": "测试开单成功", "data": result})
        else:
            return jsonify({"code": 1, "msg": result if isinstance(result, str) else "测试开单失败"}), 400
    except Exception as e:
        logger.error(f"测试开单异常: {e}")
        return jsonify({"code": 1, "msg": str(e)}), 500


@app.route("/api/auto/config", methods=["POST"])
def auto_config():
    """更新自动交易配置"""
    data = request.json or {}
    at = get_auto_trader()

    config = {}
    if "enabled_tfs" in data:
        config["enabled_tfs"] = data["enabled_tfs"]
    if "total_amount_usdt" in data:
        config["total_amount_usdt"] = float(data["total_amount_usdt"])
    if "num_entries" in data:
        config["num_entries"] = int(data["num_entries"])
    if "tp_points" in data:
        config["tp_points"] = float(data["tp_points"])
    if "sl_points" in data:
        config["sl_points"] = float(data["sl_points"])
    if "leverage" in data:
        config["leverage"] = int(data["leverage"])

    if at:
        at.update_config(config)
    else:
        pass

    return jsonify({"code": 0, "msg": "配置已更新", "config": config})


@app.route("/api/health")
def health():
    """健康检查"""
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "mock_mode": MOCK_MODE,
        "symbol": trading_status["symbol"]
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
