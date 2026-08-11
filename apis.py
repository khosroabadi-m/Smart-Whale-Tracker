"""
External API helpers: DexScreener + Etherscan V2 (multi-chain).
"""
import time
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime

import requests

import config as cfg

logger = logging.getLogger(__name__)


def _get(url: str, params: Optional[Dict] = None, timeout: int = None) -> Optional[Any]:
    timeout = timeout or cfg.REQUEST_TIMEOUT
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.Timeout:
        logger.warning("Timeout: %s", url[:80])
    except requests.exceptions.RequestException as e:
        logger.warning("Request error %s: %s", url[:60], e)
    except ValueError as e:
        logger.warning("JSON error: %s", e)
    return None


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


def get_token_price(contract: str, chain: str = "") -> Optional[float]:
    """Return current USD price for a token contract, or None."""
    if not contract:
        return None
    data = _get(cfg.DEXSCREENER_TOKEN.format(address=contract))
    if not data:
        return None
    pairs = data.get("pairs") or []
    if not pairs:
        return None
    # prefer pair on matching chain
    chain = chain.lower()
    for p in pairs:
        if chain and p.get("chainId", "").lower() != chain:
            continue
        try:
            return float(p.get("priceUsd") or 0)
        except (TypeError, ValueError):
            continue
    # fallback first pair
    try:
        return float(pairs[0].get("priceUsd") or 0)
    except (TypeError, ValueError, IndexError):
        return None


def is_valid_token(token: Dict) -> bool:
    chain = (token.get("chain") or "").lower()
    if chain not in cfg.SUPPORTED_CHAINS:
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
    chain = chain.lower()
    if chain in ("bsc", "bnb"):
        return cfg.BSCSCAN_API_KEY or cfg.ETHERSCAN_API_KEY
    return cfg.ETHERSCAN_API_KEY


def _chain_id(chain: str) -> Optional[int]:
    return cfg.CHAIN_MAP.get(chain.lower())


def get_token_transfers(
    contract: str,
    chain: str,
    sort: str = "asc",
    page: int = 1,
    offset: int = 100,
) -> List[Dict]:
    """Fetch token transfer events for a contract (oldest first by default)."""
    cid = _chain_id(chain)
    key = _api_key_for_chain(chain)
    if not cid or not key:
        logger.warning("Missing chain id or API key for %s", chain)
        return []

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
    if not data or data.get("status") != "1":
        msg = (data or {}).get("message") or (data or {}).get("result") or "unknown"
        logger.debug("tokentx empty/error for %s: %s", contract[:12], msg)
        return []
    result = data.get("result")
    return result if isinstance(result, list) else []


def get_wallet_token_transfers(
    wallet: str,
    contract: str,
    chain: str,
    sort: str = "asc",
) -> List[Dict]:
    """Transfers of a specific token involving a wallet."""
    cid = _chain_id(chain)
    key = _api_key_for_chain(chain)
    if not cid or not key:
        return []

    params = {
        "chainid": cid,
        "module": "account",
        "action": "tokentx",
        "contractaddress": contract,
        "address": wallet,
        "page": 1,
        "offset": 100,
        "sort": sort,
        "apikey": key,
    }
    data = _get(cfg.ETHERSCAN_V2, params=params)
    time.sleep(cfg.API_SLEEP)
    if not data or data.get("status") != "1":
        return []
    result = data.get("result")
    return result if isinstance(result, list) else []


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
    Returns sell info dict or None.
    """
    txs = get_wallet_token_transfers(wallet, contract, chain, sort="asc")
    if not txs:
        return None

    total_in = 0.0
    total_out = 0.0
    last_out_ts = 0
    last_out_hash = ""

    for tx in txs:
        try:
            ts = int(tx.get("timeStamp") or 0)
            decimals = int(tx.get("tokenDecimal") or 18)
            amount = float(tx.get("value") or 0) / (10 ** decimals)
            from_a = (tx.get("from") or "").lower()
            to_a = (tx.get("to") or "").lower()
            w = wallet.lower()

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
        return None

    # approximate sold percent relative to what was acquired after buy
    acquired = total_in if total_in > 0 else total_out
    sold_pct = min(100.0, (total_out / acquired) * 100) if acquired > 0 else 100.0

    return {
        "sold_percent": sold_pct,
        "timestamp": last_out_ts,
        "hash": last_out_hash,
        "total_out": total_out,
    }


def get_wallet_all_token_transfers(
    wallet: str,
    chain: str = "ethereum",
    sort: str = "desc",
    offset: int = 100,
) -> List[Dict]:
    """Recent token transfers for a wallet (all contracts). Whale monitoring."""
    cid = _chain_id(chain)
    key = _api_key_for_chain(chain)
    if not cid or not key:
        return []

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
    if not data or data.get("status") != "1":
        return []
    result = data.get("result")
    return result if isinstance(result, list) else []


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
