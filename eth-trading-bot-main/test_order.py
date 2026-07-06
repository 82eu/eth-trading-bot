"""
OKX API 测试脚本 - 开50U ETH空单
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

# 强制使用真实API
MOCK_MODE = os.getenv("MOCK_MODE", "false")
SIMULATE = os.getenv("SIMULATE", "true").lower() == "true"
API_KEY = os.getenv("OKX_API_KEY", "")
API_SECRET = os.getenv("OKX_API_SECRET", "")
PASSPHRASE = os.getenv("OKX_PASSPHRASE", "")
LEVERAGE = int(os.getenv("LEVERAGE", "10"))
SYMBOL = "ETH-USDT-SWAP"


def main():
    print("=" * 60)
    print("OKX API 测试 - 开50U ETH空单")
    print("=" * 60)
    print(f"模拟盘模式: {SIMULATE}")
    print(f"交易对: {SYMBOL}")
    print(f"杠杆: {LEVERAGE}x")
    print()

    # 检查API密钥
    if not API_KEY or not API_SECRET or not PASSPHRASE:
        print("❌ 错误: 请确保.env文件中配置了OKX API密钥")
        print("   OKX_API_KEY")
        print("   OKX_API_SECRET")
        print("   OKX_PASSPHRASE")
        sys.exit(1)

    try:
        from okx import AccountClient, TradingClient, MarketDataClient

        print("1️⃣ 初始化API客户端...")
        account = AccountClient(
            apikey=API_KEY,
            apisecret=API_SECRET,
            passphrase=PASSPHRASE,
            simulation=SIMULATE
        )
        trade = TradingClient(
            apikey=API_KEY,
            apisecret=API_SECRET,
            passphrase=PASSPHRASE,
            simulation=SIMULATE
        )
        market = MarketDataClient()
        print("✅ 客户端初始化成功")

        # 获取行情
        print(f"\n2️⃣ 获取 {SYMBOL} 行情...")
        ticker = market.get_ticker(instId=SYMBOL)
        if ticker["code"] != "0":
            print(f"❌ 获取行情失败: {ticker}")
            sys.exit(1)

        data = ticker["data"][0]
        current_price = float(data["last"])
        print(f"✅ 当前价格: ${current_price:.2f}")

        # 查询余额
        print("\n3️⃣ 查询账户余额...")
        balance = account.get_balance()
        if balance["code"] != "0":
            print(f"❌ 查询余额失败: {balance}")
            sys.exit(1)

        for item in balance["data"]:
            details = item.get("details", [])
            for d in details:
                if d.get("ccy") == "USDT":
                    print(f"✅ USDT 可用: {d.get('availBal', '0')}")
                    print(f"   USDT 余额: {d.get('bal', '0')}")

        # 计算张数
        usdt_amount = 50
        size = (usdt_amount * LEVERAGE) / current_price
        size = round(size, 2)
        print(f"\n4️⃣ 计算开仓: {usdt_amount}U @ {current_price:.2f} = {size} 张")

        # 设置杠杆
        print("\n5️⃣ 设置10倍杠杆...")
        try:
            leverage_result = account.set_leverage(
                instId=SYMBOL,
                lever=str(LEVERAGE),
                mgnMode="cross",
                posSide="short"
            )
            if leverage_result["code"] == "0":
                print("✅ 杠杆设置成功")
            else:
                print(f"⚠️ 杠杆设置: {leverage_result.get('msg', leverage_result)}")
        except Exception as e:
            print(f"⚠️ 杠杆设置异常(可能已设置): {e}")

        # 开空单
        print(f"\n6️⃣ 开空单 (market sell)...")
        order = trade.place_order(
            instId=SYMBOL,
            tdMode="cross",
            side="sell",  # 开空 = sell
            ordType="market",
            sz=str(size),
            posSide="short"
        )

        if order["code"] == "0":
            order_id = order["data"][0]["ordId"]
            print(f"✅ 下单成功!")
            print(f"   订单ID: {order_id}")
            print(f"   方向: 做空 (short)")
            print(f"   数量: {size} 张")
            print(f"   价格: ${current_price:.2f}")
            print(f"   保证金: {usdt_amount} USDT")
            print(f"   模式: {'模拟盘' if SIMULATE else '实盘'}")

            # 查询订单状态
            print("\n7️⃣ 查询订单状态...")
            import time
            time.sleep(1)
            order_info = trade.get_order(instId=SYMBOL, ordId=order_id)
            if order_info["code"] == "0":
                info = order_info["data"][0]
                print(f"   订单状态: {info.get('state', 'unknown')}")
                print(f"   成交均价: ${info.get('avgPx', 'N/A')}")
                print(f"   成交数量: {info.get('accFillSz', '0')}")

            # 查询持仓
            print("\n8️⃣ 查询当前持仓...")
            positions = account.get_positions(instId=SYMBOL)
            if positions["code"] == "0":
                has_pos = False
                for pos in positions["data"]:
                    if float(pos.get("pos", 0)) != 0:
                        has_pos = True
                        print(f"   持仓: {pos.get('instId')}")
                        print(f"   方向: {pos.get('posSide')}")
                        print(f"   数量: {pos.get('pos')} 张")
                        print(f"   开仓价: ${pos.get('avgPx')}")
                if not has_pos:
                    print("   ⚠️ 无持仓(订单可能未成交)")

            print("\n" + "=" * 60)
            print("🎉 测试完成!")
            print("=" * 60)

            if SIMULATE:
                print("\n📝 注意: 本次为模拟盘测试，未使用真实资金")
            else:
                print("\n⚠️ 注意: 本次为实盘操作，已使用真实资金!")

            print(f"\n🔗 OKX模拟盘地址: https://www.okx.com/trade-swap/eth-usdt")
            if not SIMULATE:
                print(f"🔗 OKX实盘地址: https://www.okx.com/trade-swap/eth-usdt")

        else:
            print(f"❌ 下单失败: {order}")
            sys.exit(1)

    except Exception as e:
        print(f"❌ 发生异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
