"""
Central configuration for Crypto Wallet Bot.
All tunable parameters live here.
"""
import os

# ==================== Secrets (from environment / GitHub Secrets) ====================
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

# ==================== DexScreener filters ====================
MIN_LIQUIDITY_USD = 15_000
MIN_VOLUME_24H_USD = 3_000
MIN_CHANGE_24H = 5.0          # %
MAX_CHANGE_24H = 300.0        # % – reject extreme pumps (likely rugs)
MAX_CATEGORIES = 8
MAX_TOKENS_TO_CHECK = 15      # how many top gainers to inspect for early buyers
REPORT_COUNT = 5              # how many signals to send per run

# ==================== Early buyer detection ====================
MAX_EARLY_BUYERS = 8
MIN_BUY_AMOUNT_TOKENS = 1.0   # ignore dust
# Known routers / burn / zero – never treat as "smart wallets"
BLACKLIST_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0x0000000000000000000000000000000000000001",
    # Uniswap V2 Router
    "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",
    # Uniswap V3 Router
    "0xe592427a0aece92de3edee1f18e0157c05861564",
    "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45",
    # Sushi
    "0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f",
    # 1inch
    "0x1111111254eeb25477b68fb85ed929f73a960582",
    "0x1111111254fb6c44bac0bed2854e76f90643097d",
}

# ==================== Chain support ====================
# chainId string from DexScreener -> (numeric id for Etherscan V2, api key env name)
CHAIN_MAP = {
    "ethereum": 1,
    "eth": 1,
    "bsc": 56,
    "bnb": 56,
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

# ==================== Sell / performance rules ====================
MIN_HOLD_HOURS = 0.5          # minimum hold before counting a sell
MAX_REASONABLE_PROFIT = 80.0  # % – anything above is capped for scoring
MIN_PROFIT_FOR_WIN = 15.0     # % gain to count as winning sell
MIN_LOSS_FOR_LOSS = -8.0      # % to count as losing sell
# If price moved but no on-chain sell found, we do NOT invent a sell.

# ==================== Scoring & whitelist ====================
MIN_SCORE_FOR_WHITELIST = 55.0
MIN_TRADES_FOR_WHITELIST = 3
MIN_SELLS_FOR_SCORE = 1
MAX_AGE_DAYS = 45             # drop inactive wallets after this many days

# Score weights (must sum ~1.0)
WEIGHT_WIN_RATE = 0.45
WEIGHT_AVG_PROFIT = 0.25
WEIGHT_TIMING = 0.15
WEIGHT_ACTIVITY = 0.15

# ==================== API ====================
DEXSCREENER_TRENDING = "https://api.dexscreener.com/metas/trending/v1"
DEXSCREENER_META = "https://api.dexscreener.com/metas/meta/v1"
DEXSCREENER_TOKEN = "https://api.dexscreener.com/latest/dex/tokens/{address}"
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search?q={query}"
ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"

REQUEST_TIMEOUT = 18
API_SLEEP = 0.35              # polite delay between Etherscan calls
