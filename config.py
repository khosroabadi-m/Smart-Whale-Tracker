"""
Central configuration for Crypto Wallet Bot + Whale tracker.
"""
import os

# ==================== Secrets ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")

# ==================== Paths ====================
DATA_DIR = "data"
WALLETS_FILE = os.path.join(DATA_DIR, "wallets.csv")
TRADES_FILE = os.path.join(DATA_DIR, "trades.csv")
SELLS_FILE = os.path.join(DATA_DIR, "sells.csv")
WHITELIST_FILE = os.path.join(DATA_DIR, "whitelist.csv")
WHALES_FILE = os.path.join(DATA_DIR, "whales.csv")
ALERTS_FILE = os.path.join(DATA_DIR, "whale_alerts.csv")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
NIGHTLY_LOG_FILE = os.path.join(DATA_DIR, "nightly_log.csv")
NIGHTLY_LOG_RETENTION_DAYS = 30
MAX_TRADES_PER_NIGHTLY = 500  # open trades with contract to check

# ==================== DexScreener filters ====================
MIN_LIQUIDITY_USD = 15_000
MIN_VOLUME_24H_USD = 3_000
MIN_CHANGE_24H = 5.0
MAX_CHANGE_24H = 300.0
MAX_CATEGORIES = 8
MAX_TOKENS_TO_CHECK = 12
REPORT_COUNT = 5              # discovery signals per bot run (early buyers)

# ==================== Early buyer detection ====================
MAX_EARLY_BUYERS = 8
MIN_BUY_AMOUNT_TOKENS = 1.0
BLACKLIST_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0x0000000000000000000000000000000000000001",
    "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",  # Uniswap V2
    "0xe592427a0aece92de3edee1f18e0157c05861564",  # Uniswap V3
    "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45",
    "0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f",  # Sushi
    "0x1111111254eeb25477b68fb85ed929f73a960582",  # 1inch
    "0x1111111254fb6c44bac0bed2854e76f90643097d",
}

# ==================== Chain support ====================
CHAIN_MAP = {
    "ethereum": 1, "eth": 1,
    "bsc": 56, "bnb": 56,
    "polygon": 137,
    "arbitrum": 42161,
    "base": 8453,
    "optimism": 10,
    "linea": 59144,
    "celo": 42220,
    "gnosis": 100,
    "avalanche": 43114,
    "fantom": 250,
}
SUPPORTED_CHAINS = set(CHAIN_MAP.keys())

# ==================== Sell / performance ====================
MIN_HOLD_HOURS = 0.5
MAX_REASONABLE_PROFIT = 80.0
MIN_PROFIT_FOR_WIN = 15.0
MIN_LOSS_FOR_LOSS = -8.0

# ==================== Scoring & whitelist ====================
MIN_SCORE_FOR_WHITELIST = 55.0
MIN_TRADES_FOR_WHITELIST = 3
MIN_SELLS_FOR_SCORE = 1
MAX_AGE_DAYS = 45

WEIGHT_WIN_RATE = 0.45
WEIGHT_AVG_PROFIT = 0.25
WEIGHT_TIMING = 0.15
WEIGHT_ACTIVITY = 0.15

# ==================== Whale promotion rules ====================
# A wallet becomes a WHALE when ALL of these are true:
WHALE_MIN_WINNING_SELLS = 2      # at least N profitable verified sells
WHALE_MIN_WIN_RATE = 60.0        # %
WHALE_MIN_SCORE = 55.0
WHALE_MIN_TRADES = 3
WHALE_MAX_INACTIVE_DAYS = 30     # must have been seen recently

# Whale monitoring
WHALE_MONITOR_MAX = 40           # max whales to scan per nightly run (rate-limit)
WHALE_LOOKBACK_HOURS = 36        # how far back to look for new transfers
WHALE_MIN_TRANSFER_USD = 50.0    # ignore dust buys/sells when pricing available
SEND_DISCOVERY_SIGNALS = True    # early-buyer signals from bot.py (weaker signals)

# ==================== API ====================
DEXSCREENER_TRENDING = "https://api.dexscreener.com/metas/trending/v1"
DEXSCREENER_META = "https://api.dexscreener.com/metas/meta/v1"
DEXSCREENER_TOKEN = "https://api.dexscreener.com/latest/dex/tokens/{address}"
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search?q={query}"
ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"

REQUEST_TIMEOUT = 18
API_SLEEP = 0.4
