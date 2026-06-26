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
SIMULATE = os.getenv("SIMULATE", "true").lower() == "true"

# 交易参数 - 默认ETH
SYMBOL = os.getenv("SYMBOL", "ETH-USDT-SWAP")  # 默认ETH永续合约
TIMEFRAME = os.getenv("TIMEFRAME", "1h")

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
