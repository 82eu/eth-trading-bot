"""
OKX量化交易配置
"""
import os
from dotenv import load_dotenv

load_dotenv()

# OKX API配置
API_KEY = os.getenv("OKX_API_KEY", "")
API_SECRET = os.getenv("OKX_API_SECRET", "")
PASSPHRASE = os.getenv("OKX_PASSPHRASE", "")

# 模拟交易还是实盘
SIMULATE = os.getenv("SIMULATE", "false").lower() == "true"

# 飞书机器人配置
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK", "")

# 交易参数 - 默认ETH
SYMBOL = os.getenv("SYMBOL", "ETH-USDT-SWAP")  # 默认ETH永续合约
TIMEFRAME = os.getenv("TIMEFRAME", "1h")

# 支持的交易币种列表（默认，可在页面上动态扩展）
SUPPORTED_SYMBOLS = ["ETH-USDT-SWAP", "BTC-USDT-SWAP"]
DEFAULT_SYMBOL = "ETH-USDT-SWAP"


def is_valid_swap_symbol(symbol):
    """校验是否为合法的永续合约币种符号，格式: XXX-USDT-SWAP"""
    if not symbol or not isinstance(symbol, str):
        return False
    symbol = symbol.upper().strip()
    parts = symbol.split("-")
    if len(parts) != 3:
        return False
    if parts[1] != "USDT" or parts[2] != "SWAP":
        return False
    # 币种名称部分必须是大写字母，1-20个字符
    base = parts[0]
    if not base or len(base) > 20 or not base.isalpha():
        return False
    return True

# 策略参数 - 均线策略
FAST_MA = int(os.getenv("FAST_MA", "10"))
SLOW_MA = int(os.getenv("SLOW_MA", "50"))

# 仓位管理
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "10000"))
POSITION_AMOUNT_USDT = float(os.getenv("POSITION_AMOUNT_USDT", "100"))  # 默认每次开100U
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.5"))

# 风控参数
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.02"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.05"))
LEVERAGE = int(os.getenv("LEVERAGE", "10"))  # 默认10倍杠杆

# 订单参数
ORDER_TYPE = os.getenv("ORDER_TYPE", "market")
