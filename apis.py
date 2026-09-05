"""
External API helpers: DexScreener + Etherscan V2 (multi-chain).
"""
import time
import logging
import random
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta, timezone

import requests

import config as cfg

logger = logging.getLogger(__name__)


# -------------------- HTTP with retry+backoff --------------------

def _get(
    url: str,
    params: Optional[Dict] = None,
    timeout: int = None,
    retries: int = 3,
    backoff_base: float = 0.8,
) -> Optional[Any]:
    """
    HTTP GET with exponential backoff + jitter.
    Retries on:
      - timeouts
      - 429 Too Many Requests (rate limit)
      - 5xx server errors
    Returns parsed JSON on success, None on persistent failure.
    """
    timeout = timeout or cfg.REQUEST_TIMEOUT
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            # Retry on 429 and 5xx
            if r.status_code == 429 or r.status_code >= 500:
                last_err = f"HTTP {r.status_code}"
                if attempt < retries:
                    sleep_for = backoff_base * (2 ** (attempt - 1)) + random.uniform(0, 0.3)
                    logger.debug("Retry %d/%d for %s (status=%d, sleep=%.1fs)",
                                 attempt, retries, url[:60], r.status_code, sleep_for)
                    time.sleep(sleep_for)
                    continue
                logger.warning("Giving up after %d retries: %s (status=%d)",
                               retries, url[:60], r.status_code)
                return None
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout:
            last_err = "timeout"
            if attempt < retries:
                sleep_for = backoff_base * (2 ** (attempt - 1)) + random.uniform(0, 0.3)
                logger.debug("Timeout retry %d/%d for %s (sleep=%.1fs)",
                             attempt, retries, url[:60], sleep_for)
                time.sleep(sleep_for)
                continue
            logger.warning("Timeout after %d retries: %s", retries, url[:80])
        except requests.exceptions.RequestException as e:
            last_err = str(e)
            if attempt < retries:
                sleep_for = backoff_base * (2 ** (attempt - 1)) + random.uniform(0, 0.3)
                time.sleep(sleep_for)
                continue
            logger.warning("Request error %s: %s", url[:60], e)
        except ValueError as e:
            logger.warning("JSON error: %s", e)
            return None
    logger.debug("All retries failed for %s: %s", url[:60], last_err)
    return None


# -------------------- Multi-chain price --------------------
# DexScreener's /tokens endpoint works for ALL chains (no API key, no per-chain config).
# We use it as a unified price source, with Etherscan-style fallback removed (was chain-specific).

# Cache prices for 60s to reduce API load (especially during nightly scans)
_PRICE_CACHE: Dict[str, tuple] = {}  # {cache_key: (timestamp, price)}
_PRICE_CACHE_TTL = 60.0  # seconds


def _price_cache_key(contract: str, chain: str) -> str:
    return f"{chain.lower()}:{contract.lower()}"


def clear_price_cache() -> None:
    """Clear the in-memory price cache (useful for tests)."""
    _PRICE_CACHE.clear()


def _geckoterminal_price(contract: str, chain: str) -> Optional[float]:
    """
    Fallback price source: GeckoTerminal API.
    Free, no API key needed, supports many chains.
    Endpoint: https://api.geckoterminal.com/api/v2/networks/{chain}/tokens/{addr}
    """
    if not contract:
        return None
    # Map our chain names to GeckoTerminal network IDs
    chain = (chain or "ethereum").lower()
    network_map = {
        "ethereum": "eth",
        "eth": "eth",
        "bsc": "bsc",
        "bnb": "bsc",
        "polygon": "polygon_pos",
        "arbitrum": "arbitrum",
        "base": "base",
        "optimism": "optimism",
        "avalanche": "avax",
        "fantom": "fantom",
        "gnosis": "xdai",
        "celo": "celo",
        "linea": "linea",
    }
    network = network_map.get(chain)
    if not network:
        return None
    url = f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{contract.lower()}"
    data = _get(url, retries=2)
    if not data:
        return None
    try:
        attrs = ((data.get("data") or {}).get("attributes") or {})
        price = float(attrs.get("price_usd") or 0)
        return price if price > 0 else None
    except (TypeError, ValueError, KeyError):
        return None


def get_token_price(contract: str, chain: str = "") -> Optional[float]:
    """
    Return current USD price for a token contract, or None.
    Chain: DexScreener (primary) → GeckoTerminal (fallback).
    Cached for 60s to reduce API calls during batch scans.
    """
    if not contract:
        return None
    contract = contract.lower()
    chain = (chain or "").lower()
    cache_key = _price_cache_key(contract, chain)

    # Cache hit?
    cached = _PRICE_CACHE.get(cache_key)
    if cached:
        ts, price = cached
        if (time.time() - ts) < _PRICE_CACHE_TTL:
            return price

    # --- Primary: DexScreener ---
    data = _get(cfg.DEXSCREENER_TOKEN.format(address=contract))
    if data:
        pairs = data.get("pairs") or []
        # prefer pair on matching chain
        for p in pairs:
            if chain and p.get("chainId", "").lower() != chain:
                continue
            try:
                price = float(p.get("priceUsd") or 0)
                if price > 0:
                    _PRICE_CACHE[cache_key] = (time.time(), price)
                    return price
            except (TypeError, ValueError):
                continue
        # fallback first pair
        try:
            price = float(pairs[0].get("priceUsd") or 0)
            if price > 0:
                _PRICE_CACHE[cache_key] = (time.time(), price)
                return price
        except (TypeError, ValueError, IndexError):
            pass

    # --- Fallback: GeckoTerminal ---
    price = _geckoterminal_price(contract, chain)
    if price and price > 0:
        _PRICE_CACHE[cache_key] = (time.time(), price)
        return price

    # Cache the None result too (avoid hammering both APIs)
    _PRICE_CACHE[cache_key] = (time.time(), None)
    return None


def get_token_price_at_timestamp(
    contract: str,
    chain: str,
    timestamp: int,
) -> Optional[float]:
    """
    BEST-EFFORT historical price lookup.
    DexScreener exposes OHLCV candles via the /dex/tokens/{addr} endpoint with
    embedded price history. We don't have a clean historical-price API for free,
    so we approximate using this fallback chain:
      1. Try DexScreener's pair-level price history (if available in 'priceChange.h24' etc.)
      2. Fallback to current price (with a warning log)
    """
    if not contract:
        return None
    contract = contract.lower()
    chain = (chain or "").lower()

    data = _get(cfg.DEXSCREENER_TOKEN.format(address=contract))
    if not data:
        return None
    pairs = data.get("pairs") or []
    if not pairs:
        return None

    # Pick the pair matching our chain
    pair = None
    if chain:
        for p in pairs:
            if p.get("chainId", "").lower() == chain:
                pair = p
                break
    if pair is None:
        pair = pairs[0]

    # If timestamp is within last 24h, use the h24 priceChange to estimate
    now_ts = int(time.time())
    age_seconds = now_ts - timestamp
    try:
        current_price = float(pair.get("priceUsd") or 0)
    except (TypeError, ValueError):
        return None
    if current_price <= 0:
        return None

    # If we have 24h change, estimate historical price
    if age_seconds <= 86400:
        try:
            h24_change = float((pair.get("priceChange") or {}).get("h24") or 0)
            # price 24h ago = current / (1 + h24_change/100)
            if h24_change != 0:
                est_price = current_price / (1 + h24_change / 100.0)
                return max(est_price, 0.0)
        except (TypeError, ValueError):
            pass

    # Fallback: return current price (logged at debug — caller may not need historical)
    return current_price


# -------------------- DexScreener --------------------

def get_trending_metas() -> List[Dict]:
    data = _get(cfg.DEXSCREENER_TRENDING)
    if not isinstance(data, list):
        return []
    return data


def get_tokens_from_meta(slug: str) -> List[Dict]:
    if not slug:
        return []
    data = _get(f"{cfg.DEXSCREENER_META}/{slug}")
    if not data:
        return []
    return data.get("pairs") or []


def is_valid_token(token: Dict) -> bool:
    chain = (token.get("chain") or "").lower()
    # Use active_chains() to filter out chains we have no API key for.
    # This prevents the "NOTOK" errors when BSCSCAN_API_KEY isn't set.
    if not getattr(cfg, "active_chains", None):
        # fallback if config wasn't reloaded
        if chain not in cfg.SUPPORTED_CHAINS:
            return False
    else:
        if chain not in cfg.active_chains():
            return False
    try:
        liq = float(token.get("liquidity") or 0)
        vol = float(token.get("volume") or 0)
        chg = float(token.get("change_24h") or 0)
    except (TypeError, ValueError):
        return False
    if liq < cfg.MIN_LIQUIDITY_USD:
        return False
    if vol < cfg.MIN_VOLUME_24H_USD:
        return False
    if chg < cfg.MIN_CHANGE_24H or chg > cfg.MAX_CHANGE_24H:
        return False
    return True


def fetch_gainers() -> List[Dict]:
    """Collect quality gainers from trending metas."""
    metas = get_trending_metas()
    if not metas:
        logger.warning("No trending metas from DexScreener")
        return []

    ranked = []
    for m in metas:
        change = 0.0
        try:
            change = float((m.get("marketCapChange") or {}).get("h24") or 0)
        except (TypeError, ValueError):
            pass
        ranked.append({
            "slug": m.get("slug"),
            "name": m.get("name") or "unknown",
            "change_24h": change,
        })
    ranked.sort(key=lambda x: x["change_24h"], reverse=True)

    gainers: List[Dict] = []
    seen = set()
    for meta in ranked[: cfg.MAX_CATEGORIES]:
        slug = meta["slug"]
        if not slug:
            continue
        pairs = get_tokens_from_meta(slug)
        for pair in pairs:
            try:
                base = pair.get("baseToken") or {}
                addr = (base.get("address") or "").lower()
                chain = (pair.get("chainId") or "").lower()
                key = f"{chain}:{addr}"
                if not addr or key in seen:
                    continue
                seen.add(key)

                price_change = pair.get("priceChange") or {}
                token = {
                    "name": base.get("name") or "UNKNOWN",
                    "symbol": base.get("symbol") or "???",
                    "chain": chain,
                    "price": float(pair.get("priceUsd") or 0),
                    "change_24h": float(price_change.get("h24") or 0),
                    "volume": float((pair.get("volume") or {}).get("h24") or 0),
                    "liquidity": float((pair.get("liquidity") or {}).get("usd") or 0),
                    "dex_url": pair.get("url") or "#",
                    "contract": addr,
                    "dex": pair.get("dexId") or "unknown",
                    "market_cap": float(pair.get("marketCap") or 0),
                    "meta_name": meta["name"],
                }
                if is_valid_token(token):
                    gainers.append(token)
            except Exception as e:
                logger.debug("pair parse error: %s", e)
                continue

    gainers.sort(key=lambda x: x["change_24h"], reverse=True)
    logger.info("Found %d quality gainers", len(gainers))
    return gainers


# -------------------- Etherscan V2 --------------------

def _api_key_for_chain(chain: str) -> str:
    """
    Return the API key to use for a given chain.

    Etherscan V2 API is a UNIFIED endpoint (https://api.etherscan.io/v2/api).
    It accepts a `chainid` parameter to select the chain and uses a SINGLE
    Etherscan.io API key for ALL chains (Ethereum, BSC, Polygon, Arbitrum, etc.).

    The BSCSCAN_API_KEY from bscscan.com is for the OLD endpoint
    (https://api.bscscan.com/api) and does NOT work with V2.

    So: ALWAYS return ETHERSCAN_API_KEY here. The chainid parameter in the
    request URL selects the chain — no separate keys needed per chain.

    The BSCSCAN_API_KEY config value is kept for backwards compatibility
    but is no longer used.
    """
    return cfg.ETHERSCAN_API_KEY


def _chain_id(chain: str) -> Optional[int]:
    return cfg.CHAIN_MAP.get(chain.lower())


# DEPRECATED: Native per-chain API endpoints (BscScan, PolygonScan, etc.)
# These are NO LONGER used because Etherscan V2 is the unified endpoint.
# A single ETHERSCAN_API_KEY works for ALL chains via V2.
# See: https://docs.etherscan.io/v2-migration
# Note: On the Free tier, some chains (BSC, Base, OP, Avalanche) return
# "Free API access is not supported for this chain" — these are filtered
# out by config.active_chains() before we even call the API.
_NATIVE_API_ENDPOINTS: Dict[str, tuple] = {}  # empty — V2 unified endpoint handles everything


def _native_api_key(chain: str) -> Optional[str]:
    """Deprecated: V2 unified endpoint uses a single ETHERSCAN_API_KEY for all chains."""
    return None


def get_token_transfers(
    contract: str,
    chain: str,
    sort: str = "asc",
    page: int = 1,
    offset: int = 100,
) -> List[Dict]:
    """Fetch token transfer events for a contract via Etherscan V2 unified endpoint.

    V2 uses a single API key (ETHERSCAN_API_KEY) + chainid parameter for all chains.
    Per-chain keys (BscScan, PolygonScan) are NOT valid for V2 — they return "Invalid API Key".

    Note: On the Free tier, BSC/Base/OP/Avalanche return "Free API access is not supported
    for this chain". These chains are filtered out by config.active_chains() before we
    even try to call the API.
    """
    cid = _chain_id(chain)
    key = _api_key_for_chain(chain)
    if not cid or not key:
        logger.warning("Missing chain id or API key for %s", chain)
        return []

    chain_lower = chain.lower()
    params = {
        "chainid": cid,
        "module": "account",
        "action": "tokentx",
        "contractaddress": contract,
        "page": page,
        "offset": offset,
        "sort": sort,
        "apikey": key,
    }
    data = _get(cfg.ETHERSCAN_V2, params=params)
    time.sleep(cfg.API_SLEEP)
    if data and data.get("status") == "1":
        result = data.get("result")
        return result if isinstance(result, list) else []

    # V2 failed — log the ACTUAL error from the `result` field (not just "NOTOK")
    v2_msg = (data or {}).get("message") or "no_response"
    v2_result = str((data or {}).get("result") or "")[:150]
    logger.warning(
        "Etherscan V2 tokentx fail chain=%s contract=%s: msg=%s result=%s",
        chain_lower, contract[:10], v2_msg, v2_result,
    )
    return []


def _blockscout_wallet_token_transfers(
    wallet: str,
    contract: str,
    sort: str = "asc",
) -> List[Dict]:
    """
    Fallback for Ethereum mainnet when Etherscan returns empty/errors.
    Normalizes Blockscout items to Etherscan-like dicts (timeStamp, from, to, value, tokenDecimal, hash).
    """
    url = (
        f"https://eth.blockscout.com/api/v2/addresses/{wallet}/token-transfers"
        f"?type=ERC-20&token={contract}"
    )
    data = _get(url)
    if not data or not isinstance(data, dict):
        return []
    items = data.get("items") or []
    out: List[Dict] = []
    for it in items:
        try:
            fr = ((it.get("from") or {}).get("hash") or "").lower()
            to = ((it.get("to") or {}).get("hash") or "").lower()
            ts_raw = it.get("timestamp") or ""
            # 2026-08-22T12:16:35.000000Z
            dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            unix = int(dt.timestamp())
            total = it.get("total") or {}
            val = str(total.get("value") or "0")
            dec = total.get("decimals")
            if dec is None:
                dec = (it.get("token") or {}).get("decimals") or 18
            out.append({
                "timeStamp": str(unix),
                "from": fr,
                "to": to,
                "value": val,
                "tokenDecimal": str(dec),
                "hash": it.get("transaction_hash") or "",
                "contractAddress": contract.lower(),
                "_src": "blockscout",
            })
        except Exception:
            continue
    reverse = sort.lower() == "desc"
    out.sort(key=lambda x: int(x.get("timeStamp") or 0), reverse=reverse)
    return out


def get_wallet_token_transfers(
    wallet: str,
    contract: str,
    chain: str,
    sort: str = "asc",
) -> List[Dict]:
    """
    Transfers of a specific token involving a wallet.
    Tries Etherscan V2 first; on empty/error for ethereum, falls back to Blockscout.
    """
    wallet = (wallet or "").lower().strip()
    contract = (contract or "").lower().strip()
    chain = (chain or "ethereum").lower()
    if not wallet or not contract:
        return []

    cid = _chain_id(chain)
    key = _api_key_for_chain(chain)
    txs: List[Dict] = []

    if cid and key:
        params = {
            "chainid": cid,
            "module": "account",
            "action": "tokentx",
            "contractaddress": contract,
            "address": wallet,
            "page": 1,
            "offset": 200,  # recent window; use sort=desc for sells
            "sort": sort,
            "apikey": key,
        }
        data = _get(cfg.ETHERSCAN_V2, params=params)
        time.sleep(cfg.API_SLEEP)
        if data and data.get("status") == "1":
            result = data.get("result")
            if isinstance(result, list) and result:
                return result
            logger.info(
                "Etherscan empty list wallet=%s… token=%s… msg=%s",
                wallet[:10], contract[:10], (data.get("message") or data.get("result") or "")[:60],
            )
        else:
            msg = (data or {}).get("message") or (data or {}).get("result") or "no_response"
            logger.warning(
                "Etherscan fail wallet=%s… token=%s… status=%s msg=%s",
                wallet[:10], contract[:10], (data or {}).get("status"), str(msg)[:80],
            )
    else:
        logger.warning("Missing chain id or API key for %s – skip Etherscan", chain)

    # Fallback: Blockscout (ethereum only)
    if chain in ("ethereum", "eth"):
        logger.debug("Blockscout fallback wallet=%s… token=%s…", wallet[:10], contract[:10])
        txs = _blockscout_wallet_token_transfers(wallet, contract, sort=sort)
        time.sleep(0.15)
        if txs:
            logger.debug("Blockscout returned %d txs for %s…", len(txs), wallet[:10])
        return txs

    return []


def find_early_buyers(contract: str, chain: str) -> List[Dict]:
    """
    Return earliest unique buyers of a token.
    A buyer is the `to` address of an early transfer with meaningful amount,
    excluding blacklisted / router / zero addresses.
    """
    if not contract or len(contract) < 10:
        return []

    txs = get_token_transfers(contract, chain, sort="asc", offset=150)
    if not txs:
        return []

    buyers: Dict[str, Dict] = {}
    for tx in txs:
        try:
            to_addr = (tx.get("to") or "").lower()
            from_addr = (tx.get("from") or "").lower()
            if not to_addr or to_addr in cfg.BLACKLIST_ADDRESSES:
                continue
            if to_addr == contract.lower():  # token contract itself
                continue
            # skip if already recorded
            if to_addr in buyers:
                continue

            decimals = int(tx.get("tokenDecimal") or 18)
            raw = float(tx.get("value") or 0)
            amount = raw / (10 ** decimals)
            if amount < cfg.MIN_BUY_AMOUNT_TOKENS:
                continue

            # prefer transfers that look like buys (from router / pair / unknown)
            buyers[to_addr] = {
                "address": to_addr,
                "amount": amount,
                "timestamp": int(tx.get("timeStamp") or 0),
                "hash": tx.get("hash") or "",
                "from": from_addr,
            }
            if len(buyers) >= cfg.MAX_EARLY_BUYERS:
                break
        except (ValueError, TypeError, KeyError):
            continue

    result = list(buyers.values())
    result.sort(key=lambda x: x["timestamp"])
    return result


def detect_onchain_sell(
    wallet: str,
    contract: str,
    chain: str,
    buy_timestamp: int,
) -> Optional[Dict]:
    """
    Look for outgoing token transfers from wallet after buy_timestamp.

    Returns:
      - dict with sold_percent/timestamp/hash/total_out on real sell
      - {"_status": "empty_api"} when no transfer data from any source
      - {"_status": "no_sell"} when history exists but no OUT after buy
      - None only on unexpected failure (treated as empty)
    """
    # sort=desc: newest first — critical so sells AFTER buy are in the first page
    txs = get_wallet_token_transfers(wallet, contract, chain, sort="desc")
    if not txs:
        return {"_status": "empty_api"}

    total_in = 0.0
    total_out = 0.0
    last_out_ts = 0
    last_out_hash = ""
    w = wallet.lower()

    for tx in txs:
        try:
            ts = int(tx.get("timeStamp") or 0)
            decimals = int(tx.get("tokenDecimal") or 18)
            amount = float(tx.get("value") or 0) / (10 ** decimals)
            from_a = (tx.get("from") or "").lower()
            to_a = (tx.get("to") or "").lower()

            if ts < buy_timestamp - 60:  # small clock skew tolerance
                continue

            if to_a == w:
                total_in += amount
            if from_a == w:
                total_out += amount
                last_out_ts = ts
                last_out_hash = tx.get("hash") or ""
        except (ValueError, TypeError):
            continue

    if total_out <= 0:
        # Second chance: Etherscan may only have old pages; force Blockscout for eth
        if (chain or "").lower() in ("ethereum", "eth") and not any(
            (tx.get("_src") == "blockscout") for tx in txs
        ):
            bs = _blockscout_wallet_token_transfers(wallet, contract, sort="desc")
            if bs:
                txs = bs
                total_in = total_out = 0.0
                last_out_ts = 0
                last_out_hash = ""
                for tx in txs:
                    try:
                        ts = int(tx.get("timeStamp") or 0)
                        decimals = int(tx.get("tokenDecimal") or 18)
                        amount = float(tx.get("value") or 0) / (10 ** decimals)
                        from_a = (tx.get("from") or "").lower()
                        to_a = (tx.get("to") or "").lower()
                        if ts < buy_timestamp - 60:
                            continue
                        if to_a == w:
                            total_in += amount
                        if from_a == w:
                            total_out += amount
                            last_out_ts = ts
                            last_out_hash = tx.get("hash") or ""
                    except (ValueError, TypeError):
                        continue
        if total_out <= 0:
            return {"_status": "no_sell", "tx_count": len(txs)}

    acquired = total_in if total_in > 0 else total_out
    sold_pct = min(100.0, (total_out / acquired) * 100) if acquired > 0 else 100.0

    return {
        "sold_percent": sold_pct,
        "timestamp": last_out_ts,
        "hash": last_out_hash,
        "total_out": total_out,
        "tx_count": len(txs),
    }


def get_wallet_all_token_transfers(
    wallet: str,
    chain: str = "ethereum",
    sort: str = "desc",
    offset: int = 100,
) -> List[Dict]:
    """Recent token transfers for a wallet (all contracts). Whale monitoring + backfill.

    Uses Etherscan V2 unified endpoint with ETHERSCAN_API_KEY + chainid parameter.
    No per-chain keys needed — V2 handles all chains with a single key.

    Note: On the Free tier, BSC/Base/OP/Avalanche chains return
    "Free API access is not supported for this chain".
    """
    cid = _chain_id(chain)
    key = _api_key_for_chain(chain)
    if not cid or not key:
        logger.warning("Missing chain id or API key for %s — skip", chain)
        return []

    chain_lower = chain.lower()
    params = {
        "chainid": cid,
        "module": "account",
        "action": "tokentx",
        "address": wallet,
        "page": 1,
        "offset": offset,
        "sort": sort,
        "apikey": key,
    }
    data = _get(cfg.ETHERSCAN_V2, params=params)
    time.sleep(cfg.API_SLEEP)
    if data and data.get("status") == "1":
        result = data.get("result")
        if isinstance(result, list):
            logger.debug("Etherscan V2 returned %d transfers for wallet=%s… chain=%s",
                         len(result), wallet[:10], chain)
            return result
        logger.warning("Etherscan V2 returned non-list result for wallet=%s… chain=%s", wallet[:10], chain)
        return []

    # V2 failed — log the actual error from the `result` field
    v2_msg = (data or {}).get("message") or "no_response"
    v2_result = str((data or {}).get("result") or "")[:150]
    logger.warning(
        "Etherscan V2 wallet tokentx fail chain=%s wallet=%s: msg=%s result=%s",
        chain_lower, wallet[:10], v2_msg, v2_result,
    )
    return []


def parse_whale_activity(
    wallet: str,
    chain: str,
    since_ts: int,
) -> List[Dict]:
    """
    Buy/sell events for a whale after since_ts.
    {type, token_symbol, token_name, contract, amount, timestamp, hash, chain}
    """
    txs = get_wallet_all_token_transfers(wallet, chain, sort="desc", offset=80)
    if not txs:
        return []

    events = []
    w = wallet.lower()
    for tx in txs:
        try:
            ts = int(tx.get("timeStamp") or 0)
            if ts < since_ts:
                continue
            decimals = int(tx.get("tokenDecimal") or 18)
            amount = float(tx.get("value") or 0) / (10 ** decimals)
            if amount <= 0:
                continue
            from_a = (tx.get("from") or "").lower()
            to_a = (tx.get("to") or "").lower()
            contract = (tx.get("contractAddress") or "").lower()
            symbol = tx.get("tokenSymbol") or "???"
            name = tx.get("tokenName") or symbol

            if to_a == w and from_a != w:
                etype = "buy"
            elif from_a == w and to_a != w:
                etype = "sell"
            else:
                continue

            events.append({
                "type": etype,
                "token_symbol": symbol,
                "token_name": name,
                "contract": contract,
                "amount": amount,
                "timestamp": ts,
                "hash": tx.get("hash") or "",
                "chain": chain,
            })
        except (ValueError, TypeError):
            continue

    events.sort(key=lambda x: x["timestamp"])
    return events


# -------------------- Backfill mode --------------------
# Breaks the chicken-and-egg: most wallets have only 1 trade recorded.
# When a wallet reaches 1 verified profitable sell, look back N days into
# their on-chain history to find OTHER profitable sells we missed.

def backfill_wallet_sells(
    wallet: str,
    chain: str,
    days_back: int = 30,
    max_tokens: int = 25,
) -> List[Dict]:
    """
    Look back N days into wallet's token-transfer history.
    Identify (contract, buy_ts, sell_ts, sold_percent) pairs we missed.

    Returns list of dicts:
      {contract, token_symbol, buy_timestamp, sell_timestamp, sell_hash,
       buy_hash, sold_percent, amount_out, amount_in}

    Only returns pairs where the wallet actually SOLD tokens they had bought
    within the lookback window.

    NOTE: this does NOT record sells; the caller is responsible for persisting
    via db.add_sell() to avoid coupling API logic with storage.
    """
    from datetime import datetime, timedelta, timezone

    wallet = (wallet or "").lower().strip()
    chain = (chain or "ethereum").lower()
    if not wallet:
        return []

    # fetch up to 200 transfers — sort="desc" to get NEWEST first (was "asc" = oldest, bug!)
    txs = get_wallet_all_token_transfers(wallet, chain, sort="desc", offset=200)
    if not txs and chain in ("ethereum", "eth"):
        # try Blockscout fallback (more generous history)
        txs = _blockscout_wallet_token_transfers_all(wallet)
    if not txs:
        logger.info(
            "Backfill %s: 0 transfers from API (chain=%s) — wallet may be inactive "
            "or API rate-limited",
            wallet[:10], chain,
        )
        return []

    logger.info(
        "Backfill %s: got %d raw transfers (chain=%s), filtering last %d days",
        wallet[:10], len(txs), chain, days_back,
    )

    cutoff_ts = int((datetime.now(timezone.utc).replace(tzinfo=None) -
                     timedelta(days=days_back)).timestamp())

    # group transfers by contract
    by_contract: Dict[str, List[Dict]] = {}
    skipped_old = 0
    for tx in txs:
        try:
            ts = int(tx.get("timeStamp") or 0)
            if ts < cutoff_ts:
                skipped_old += 1
                continue
            contract = (tx.get("contractAddress") or "").lower()
            if not contract:
                continue
            by_contract.setdefault(contract, []).append(tx)
        except (ValueError, TypeError):
            continue

    # for each contract, find buy-then-sell pairs
    found: List[Dict] = []
    w = wallet.lower()
    for contract, ctx_txs in by_contract.items():
        if len(found) >= max_tokens:
            break
        # FILTER OUT base tokens (WETH, USDC, USDT, DAI, etc.)
        # These are "medium of exchange" tokens — every DEX swap involves them.
        # Treating WETH in→WETH out as a "trade" pollutes data with 0% profit phantom sells.
        if hasattr(cfg, "BASE_TOKENS") and contract in cfg.BASE_TOKENS:
            logger.debug("Backfill %s: skipping base token %s", wallet[:10], contract[:10])
            continue
        ctx_txs.sort(key=lambda x: int(x.get("timeStamp") or 0))
        total_in = 0.0
        total_out = 0.0
        first_buy_ts = 0
        first_buy_hash = ""
        last_sell_ts = 0
        last_sell_hash = ""
        symbol = ctx_txs[0].get("tokenSymbol") or "???"
        decimals = int(ctx_txs[0].get("tokenDecimal") or 18)

        for tx in ctx_txs:
            try:
                ts = int(tx.get("timeStamp") or 0)
                amount = float(tx.get("value") or 0) / (10 ** decimals)
                from_a = (tx.get("from") or "").lower()
                to_a = (tx.get("to") or "").lower()
                if to_a == w and from_a != w:
                    total_in += amount
                    if first_buy_ts == 0:
                        first_buy_ts = ts
                        first_buy_hash = tx.get("hash") or ""
                elif from_a == w and to_a != w:
                    total_out += amount
                    last_sell_ts = ts
                    last_sell_hash = tx.get("hash") or ""
            except (ValueError, TypeError):
                continue

        # we have a sell only if total_out > 0
        if total_out <= 0 or last_sell_ts == 0:
            continue
        acquired = total_in if total_in > 0 else total_out
        sold_pct = min(100.0, (total_out / acquired) * 100.0) if acquired > 0 else 100.0
        if sold_pct < 10.0:
            continue  # dust sell

        found.append({
            "contract": contract,
            "token_symbol": symbol,
            "buy_timestamp": first_buy_ts,
            "buy_hash": first_buy_hash,
            "sell_timestamp": last_sell_ts,
            "sell_hash": last_sell_hash,
            "sold_percent": sold_pct,
            "amount_in": total_in,
            "amount_out": total_out,
        })

    found.sort(key=lambda x: x["sell_timestamp"], reverse=True)
    logger.info(
        "Backfill %s: %d contracts analyzed, %d had sells (skipped_old=%d, dust_filtered)",
        wallet[:10], len(by_contract), len(found), skipped_old,
    )
    return found


def backfill_wallet_buys(
    wallet: str,
    chain: str,
    days_back: int = 30,
    max_tokens: int = 25,
) -> List[Dict]:
    """
    Look back N days into wallet's token-transfer history.
    Find ALL contracts the wallet BOUGHT (received tokens), even if they haven't sold yet.

    This is more comprehensive than backfill_wallet_sells() which only returns contracts
    where the wallet both bought AND sold. Many wallets buy multiple tokens but only sell
    some of them — those unsold tokens still count as "trades" for whale qualification.

    Returns list of dicts:
      {contract, token_symbol, buy_timestamp, buy_hash, amount_in, has_sell}
    """
    from datetime import datetime, timedelta, timezone

    wallet = (wallet or "").lower().strip()
    chain = (chain or "ethereum").lower()
    if not wallet:
        return []

    # fetch up to 200 transfers — sort="desc" to get NEWEST first
    txs = get_wallet_all_token_transfers(wallet, chain, sort="desc", offset=200)
    if not txs and chain in ("ethereum", "eth"):
        txs = _blockscout_wallet_token_transfers_all(wallet)
    if not txs:
        logger.info(
            "Backfill buys %s: 0 transfers from API (chain=%s)",
            wallet[:10], chain,
        )
        return []

    logger.info(
        "Backfill buys %s: got %d raw transfers (chain=%s), filtering last %d days",
        wallet[:10], len(txs), chain, days_back,
    )

    cutoff_ts = int((datetime.now(timezone.utc).replace(tzinfo=None) -
                     timedelta(days=days_back)).timestamp())

    # group transfers by contract
    by_contract: Dict[str, List[Dict]] = {}
    skipped_old = 0
    for tx in txs:
        try:
            ts = int(tx.get("timeStamp") or 0)
            if ts < cutoff_ts:
                skipped_old += 1
                continue
            contract = (tx.get("contractAddress") or "").lower()
            if not contract:
                continue
            by_contract.setdefault(contract, []).append(tx)
        except (ValueError, TypeError):
            continue

    # for each contract, check if wallet received tokens (bought)
    found: List[Dict] = []
    w = wallet.lower()
    for contract, ctx_txs in by_contract.items():
        if len(found) >= max_tokens:
            break
        # FILTER OUT base tokens
        if hasattr(cfg, "BASE_TOKENS") and contract in cfg.BASE_TOKENS:
            continue
        ctx_txs.sort(key=lambda x: int(x.get("timeStamp") or 0))
        total_in = 0.0
        total_out = 0.0
        first_buy_ts = 0
        first_buy_hash = ""
        symbol = ctx_txs[0].get("tokenSymbol") or "???"
        decimals = int(ctx_txs[0].get("tokenDecimal") or 18)

        for tx in ctx_txs:
            try:
                ts = int(tx.get("timeStamp") or 0)
                amount = float(tx.get("value") or 0) / (10 ** decimals)
                from_a = (tx.get("from") or "").lower()
                to_a = (tx.get("to") or "").lower()
                if to_a == w and from_a != w:
                    total_in += amount
                    if first_buy_ts == 0:
                        first_buy_ts = ts
                        first_buy_hash = tx.get("hash") or ""
                elif from_a == w and to_a != w:
                    total_out += amount
            except (ValueError, TypeError):
                continue

        # we have a buy only if total_in > 0
        if total_in <= 0:
            continue
        # skip dust buys
        if total_in < 0.001:
            continue

        found.append({
            "contract": contract,
            "token_symbol": symbol,
            "buy_timestamp": first_buy_ts,
            "buy_hash": first_buy_hash,
            "amount_in": total_in,
            "amount_out": total_out,
            "has_sell": total_out > 0,
        })

    found.sort(key=lambda x: x["buy_timestamp"], reverse=True)
    logger.info(
        "Backfill buys %s: %d contracts analyzed, %d had buys (skipped_old=%d)",
        wallet[:10], len(by_contract), len(found), skipped_old,
    )
    return found


def _blockscout_wallet_token_transfers_all(wallet: str) -> List[Dict]:
    """
    Blockscout fallback for Ethereum: fetch ALL recent token transfers
    (not filtered by contract). Returns Etherscan-like dicts.
    """
    url = f"https://eth.blockscout.com/api/v2/addresses/{wallet}/token-transfers"
    data = _get(url)
    if not data or not isinstance(data, dict):
        return []
    items = data.get("items") or []
    out: List[Dict] = []
    for it in items:
        try:
            fr = ((it.get("from") or {}).get("hash") or "").lower()
            to = ((it.get("to") or {}).get("hash") or "").lower()
            ts_raw = it.get("timestamp") or ""
            dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            unix = int(dt.timestamp())
            total = it.get("total") or {}
            val = str(total.get("value") or "0")
            dec = total.get("decimals")
            if dec is None:
                dec = (it.get("token") or {}).get("decimals") or 18
            token_obj = it.get("token") or {}
            symbol = token_obj.get("symbol") or "???"
            contract_addr = (token_obj.get("address") or "").lower()
            out.append({
                "timeStamp": str(unix),
                "from": fr,
                "to": to,
                "value": val,
                "tokenDecimal": str(dec),
                "hash": it.get("transaction_hash") or "",
                "contractAddress": contract_addr,
                "tokenSymbol": symbol,
                "tokenName": token_obj.get("name") or symbol,
                "_src": "blockscout",
            })
        except Exception:
            continue
    return out
