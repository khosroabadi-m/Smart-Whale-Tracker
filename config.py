"""
Central configuration for Crypto Wallet Bot + Whale tracker.
"""
import os

# ==================== Secrets ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "")  # native BSC fallback (api.bscscan.com)
# Native per-chain API keys (used as fallback when Etherscan V2 returns NOTOK)
POLYGONSCAN_API_KEY = os.getenv("POLYGONSCAN_API_KEY", "")
ARBISCAN_API_KEY = os.getenv("ARBISCAN_API_KEY", "")
BASESCAN_API_KEY = os.getenv("BASESCAN_API_KEY", "")
OPTIMISM_API_KEY = os.getenv("OPTIMISM_API_KEY", "")
SNOWTRACE_API_KEY = os.getenv("SNOWTRACE_API_KEY", "")  # Avalanche
FTMSCAN_API_KEY = os.getenv("FTMSCAN_API_KEY", "")      # Fantom
GNOSISSCAN_API_KEY = os.getenv("GNOSISSCAN_API_KEY", "")
CELOSCAN_API_KEY = os.getenv("CELOSCAN_API_KEY", "")
LINEASCAN_API_KEY = os.getenv("LINEASCAN_API_KEY", "")

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

# ==================== Base tokens (filtered from backfill) ====================
# These are "medium of exchange" tokens, NOT real trading targets.
# Every DEX swap involves WETH/USDC/USDT in/out — treating them as "buys/sells"
# pollutes the data with 0% profit phantom trades.
# Source: Ethereum mainnet contract addresses.
BASE_TOKENS = {
    # Ethereum mainnet
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH (Wrapped Ether)
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC
    "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT
    "0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI
    "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",  # WBTC
    "0xc00e94cb662c3520282e6f5717214004a7f26888",  # COMP
    "0x514910771af9ca656af840dff83e8264ecf986ca",  # LINK
    "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",  # UNI
    "0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2",  # MKR
    "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9",  # AAVE
    # Common stables
    "0x853d955acef822db058eb8505911ed77f175b99e",  # FRAX
    "0x0000000000085d4780b73119b644ae5ecd22b376",  # TrueUSD
    "0x4fabb145d64652a948d72533023f6e7a623c7c53",  # BUSD
    "0x8e870d67f660d95d5be534091c47d22a45a851d4",  # PAX
}

# ==================== Chain support ====================
# Source: https://docs.etherscan.io/v2-migration (Etherscan V2 unified API)
#
# V2 uses a SINGLE endpoint (https://api.etherscan.io/v2/api) + a SINGLE
# ETHERSCAN_API_KEY for ALL chains. The `chainid` parameter selects the chain.
# Per-chain keys (BscScan, PolygonScan, Arbiscan, etc.) are NOT valid for V2.
#
# Rate limits (Free tier): 3 calls/sec, 100k calls/day
# Some chains require a PAID plan (Lite+). See PAID_TIER_ONLY below.
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
    # New chains added in V2 (all free tier)
    "unichain": 130,
    "monad": 143,
    "sonic": 146,
    "bittorrent": 199,
    "opbnb": 204,
    "fraxtal": 252,
    "world": 480,
    "stable": 988,
    "hyperevm": 999,
    "sei": 1329,
    "abstract": 2741,
    "megaeth": 4326,
    "memecore": 4352,
    "mantle": 5000,
    "plasma": 9745,
    "apechain": 33139,
    "berachain": 80094,
    "blast": 81457,
    "taiko": 167000,
    "katana": 747474,
}
SUPPORTED_CHAINS = set(CHAIN_MAP.keys())

# Chains that require a PAID Etherscan plan (Lite, Standard, Advanced, Pro, etc.)
# On the Free tier, calls to these chains return:
#   "Free API access is not supported for this chain. Please upgrade your api plan..."
# Source: https://docs.etherscan.io/v2-migration (supported-chains page)
PAID_TIER_ONLY = {
    "bsc", "bnb",        # BNB Smart Chain
    "base",              # Base Mainnet
    "optimism",          # OP Mainnet
    "avalanche",         # Avalanche C-Chain
    # Note: Gnosis moves to paid on Sept 1, 2026
}

# DEPRECATED: Native per-chain API endpoints (BscScan, PolygonScan, etc.)
# These are NO LONGER used because Etherscan V2 is the unified endpoint.
# Kept here for reference only — remove after confirming V2 works.
# _NATIVE_API_ENDPOINTS = { "bsc": ("https://api.bscscan.com/api", "BSCSCAN_API_KEY"), ... }

# Rate limits per plan (Free tier is what most users have)
# Source: https://docs.etherscan.io/v2-migration (rate-limits page)
RATE_LIMIT_CALLS_PER_SEC = {
    "free": 3,
    "lite": 5,
    "standard": 10,
    "advanced": 20,
    "professional": 30,
}
# Set this to your plan tier; controls API_SLEEP in apis.py
ETHERSCAN_PLAN_TIER = os.getenv("ETHERSCAN_PLAN_TIER", "free").lower()

# Compute sleep time to stay under rate limit (with safety margin)
# Free tier: 3 calls/sec → 0.4s sleep keeps us at 2.5 calls/sec (safe)
_plan = ETHERSCAN_PLAN_TIER if ETHERSCAN_PLAN_TIER in RATE_LIMIT_CALLS_PER_SEC else "free"
_max_calls_per_sec = RATE_LIMIT_CALLS_PER_SEC[_plan]
API_SLEEP = max(0.15, (1.0 / _max_calls_per_sec) * 1.25)  # 25% safety margin


def active_chains() -> set:
    """
    Return set of chains we can actually query.
    On Free tier, excludes PAID_TIER_ONLY chains (BSC, Base, OP, Avalanche).
    On paid tiers, includes all chains.
    """
    if not ETHERSCAN_API_KEY:
        return set()
    if ETHERSCAN_PLAN_TIER in ("free", ""):
        return {c for c in SUPPORTED_CHAINS if c not in PAID_TIER_ONLY}
    return set(SUPPORTED_CHAINS)

# ==================== Sell / performance ====================
MIN_HOLD_HOURS = 0.5
MAX_REASONABLE_PROFIT = 80.0
MIN_PROFIT_FOR_WIN = 15.0
MIN_LOSS_FOR_LOSS = -8.0

# ==================== Scoring & whitelist ====================
MIN_SCORE_FOR_WHITELIST = 45.0   # lowered to match WHALE_MIN_SCORE (was 55)
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
WHALE_MIN_WIN_RATE = 50.0        # % (lowered from 60 — most good wallets have 1 loss)
WHALE_MIN_SCORE = 45.0           # lowered from 55 — old formula capped good wallets at ~54
WHALE_MIN_TRADES = 3
WHALE_MAX_INACTIVE_DAYS = 30     # must have been seen recently

# Whale candidate (early-stage wallet that just got 1 verified winning sell)
WHALE_CANDIDATE_MIN_WINS = 1     # 1 verified profitable sell → candidate alert
WHALE_CANDIDATE_MIN_PROFIT = 5.0 # min profit % for the qualifying sell

# Whale monitoring
WHALE_MONITOR_MAX = 40           # max whales to scan per nightly run (rate-limit)
WHALE_LOOKBACK_HOURS = 36        # how far back to look for new transfers
WHALE_MIN_TRANSFER_USD = 50.0    # ignore dust buys/sells when pricing available
SEND_DISCOVERY_SIGNALS = True    # early-buyer signals from bot.py (weaker signals)

# ==================== Backfill mode ====================
# When a wallet reaches 1 verified profitable sell, look back N days
# into their on-chain history to find OTHER profitable sells we missed.
# This breaks the chicken-and-egg: most wallets only had 1 trade recorded.
BACKFILL_ENABLED = True
BACKFILL_DAYS = 30               # how far back to fetch wallet's transfer history
BACKFILL_MAX_WALLETS_PER_RUN = 5 # don't blow API quota; only backfill top-N candidates per nightly
BACKFILL_MIN_PROFIT_USD = 0.0    # ignore dust sells (set 0 for now, can tune later)
BACKFILL_MAX_TOKENS_PER_WALLET = 25  # cap unique tokens per wallet to control price-lookup count

# ==================== Alerts ====================
ALERT_CANDIDATE_ENABLED = True   # alert when wallet reaches 1 verified winning sell
WEEKLY_SUMMARY_ENABLED = True    # include top-5 candidates in nightly report (weekly cadence)
WEEKLY_SUMMARY_DAY = 6           # 0=Mon ... 6=Sun — day to send full weekly summary

# ==================== API ====================
DEXSCREENER_TRENDING = "https://api.dexscreener.com/metas/trending/v1"
DEXSCREENER_META = "https://api.dexscreener.com/metas/meta/v1"
DEXSCREENER_TOKEN = "https://api.dexscreener.com/latest/dex/tokens/{address}"
DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search?q={query}"
ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"

REQUEST_TIMEOUT = 18
# API_SLEEP is computed above based on ETHERSCAN_PLAN_TIER (rate limit safety)
