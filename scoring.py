"""
Wallet scoring and whitelist maintenance.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Tuple

import config as cfg
import db

logger = logging.getLogger(__name__)


def _safe_float(val, default=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val, default=0) -> int:
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def calculate_wallet_metrics(wallet: Dict, sells: List[Dict]) -> Dict:
    """
    Compute win_rate, avg_profit, avg_hold, score for one wallet.
    Returns dict with metrics (does not write).
    """
    addr = (wallet.get("address") or "").lower()
    wallet_sells = [s for s in sells if (s.get("wallet_address") or "").lower() == addr]

    total_sells = len(wallet_sells)
    if total_sells == 0:
        return {
            "win_rate": 0.0,
            "avg_profit": 0.0,
            "avg_hold_duration": 0.0,
            "score": 0.0,
            "winning_sells": 0,
            "losing_sells": 0,
            "total_sells": 0,
        }

    wins = sum(1 for s in wallet_sells if (s.get("is_winning") or "").upper() == "TRUE")
    losses = total_sells - wins
    win_rate = (wins / total_sells) * 100.0

    profits = [_safe_float(s.get("profit_percent")) for s in wallet_sells]
    # cap each profit for averaging
    profits = [min(p, cfg.MAX_REASONABLE_PROFIT) for p in profits]
    avg_profit = sum(profits) / len(profits)

    holds = [_safe_float(s.get("hold_duration_hours")) for s in wallet_sells]
    avg_hold = sum(holds) / len(holds) if holds else 0.0

    # Timing: profit per hour of hold (higher = better exit timing)
    timing_raw = avg_profit / (avg_hold + 1.0)
    timing_score = max(0.0, min(100.0, timing_raw * 8.0))  # scale roughly to 0-100

    # Activity: more completed sells (with a soft cap) is better
    total_trades = _safe_int(wallet.get("total_trades"), 0)
    activity_score = min(100.0, total_sells * 12.0 + min(total_trades, 20) * 1.5)

    score = (
        win_rate * cfg.WEIGHT_WIN_RATE
        + min(avg_profit, cfg.MAX_REASONABLE_PROFIT) * cfg.WEIGHT_AVG_PROFIT
        + timing_score * cfg.WEIGHT_TIMING
        + activity_score * cfg.WEIGHT_ACTIVITY
    )
    # slight penalty if almost no real activity
    if total_sells < cfg.MIN_SELLS_FOR_SCORE:
        score *= 0.3

    return {
        "win_rate": round(win_rate, 2),
        "avg_profit": round(avg_profit, 2),
        "avg_hold_duration": round(avg_hold, 4),
        "score": round(score, 2),
        "winning_sells": wins,
        "losing_sells": losses,
        "total_sells": total_sells,
    }


def update_all_scores() -> int:
    """Recalculate metrics for every wallet. Returns count updated."""
    wallets = db.read_csv(cfg.WALLETS_FILE, db.wallet_headers())
    sells = db.read_csv(cfg.SELLS_FILE, db.sell_headers())
    updated = 0

    for w in wallets:
        metrics = calculate_wallet_metrics(w, sells)
        w["win_rate"] = str(metrics["win_rate"])
        w["avg_profit"] = str(metrics["avg_profit"])
        w["avg_hold_duration"] = str(metrics["avg_hold_duration"])
        w["score"] = str(metrics["score"])
        w["winning_sells"] = str(metrics["winning_sells"])
        w["losing_sells"] = str(metrics["losing_sells"])
        w["total_sells"] = str(metrics["total_sells"])

        score = metrics["score"]
        trades = _safe_int(w.get("total_trades"))
        if score >= cfg.MIN_SCORE_FOR_WHITELIST and trades >= cfg.MIN_TRADES_FOR_WHITELIST:
            w["in_whitelist"] = "TRUE"
        else:
            w["in_whitelist"] = "FALSE"
        w.setdefault("is_whale", "FALSE")
        updated += 1

    db.write_csv(cfg.WALLETS_FILE, db.wallet_headers(), wallets)
    logger.info("Updated scores for %d wallets", updated)
    return updated


def rebuild_whitelist() -> int:
    wallets = db.read_csv(cfg.WALLETS_FILE, db.wallet_headers())
    wl = [w for w in wallets if (w.get("in_whitelist") or "").upper() == "TRUE"]
    wl.sort(key=lambda x: _safe_float(x.get("score")), reverse=True)

    rows = []
    for rank, w in enumerate(wl, 1):
        rows.append({
            "rank": str(rank),
            "wallet_address": w.get("address", ""),
            "chain": w.get("chain", ""),
            "score": w.get("score", "0"),
            "total_trades": w.get("total_trades", "0"),
            "win_rate": w.get("win_rate", "0"),
            "avg_profit": w.get("avg_profit", "0"),
            "last_seen": w.get("last_seen", ""),
        })
    db.write_csv(cfg.WHITELIST_FILE, db.whitelist_headers(), rows)
    logger.info("Whitelist rebuilt: %d wallets", len(rows))
    return len(rows)


def cleanup_old_wallets() -> int:
    wallets = db.read_csv(cfg.WALLETS_FILE, db.wallet_headers())
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=cfg.MAX_AGE_DAYS)
    kept = []
    removed = 0
    for w in wallets:
        last = w.get("last_seen") or ""
        try:
            dt = datetime.fromisoformat(last.replace("Z", "+00:00").split("+")[0])
            if dt >= cutoff:
                kept.append(w)
            else:
                removed += 1
        except Exception:
            kept.append(w)  # keep if unparseable
    db.write_csv(cfg.WALLETS_FILE, db.wallet_headers(), kept)
    logger.info("Removed %d stale wallets", removed)
    return removed


def sanitize_existing_data() -> Dict[str, int]:
    """
    Cleanup:
    - remove blacklisted wallets/trades/sells
    - remove trades with empty/invalid contract
    - for open trades: keep only newest per wallet+contract (drop older dups)
    - cap absurd sell profits / drop fake near-zero holds
    """
    stats = {
        "wallets_removed": 0,
        "sells_capped": 0,
        "sells_removed": 0,
        "trades_removed": 0,
        "trades_no_contract": 0,
        "trades_dup_open": 0,
    }

    # wallets
    wallets = db.read_csv(cfg.WALLETS_FILE, db.wallet_headers())
    clean_w = []
    for w in wallets:
        addr = (w.get("address") or "").lower()
        if db.is_blacklisted(addr) or not addr.startswith("0x") or len(addr) < 10:
            stats["wallets_removed"] += 1
            continue
        clean_w.append(w)
    db.write_csv(cfg.WALLETS_FILE, db.wallet_headers(), clean_w)

    # sells
    sells = db.read_csv(cfg.SELLS_FILE, db.sell_headers())
    clean_s = []
    for s in sells:
        addr = (s.get("wallet_address") or "").lower()
        if db.is_blacklisted(addr):
            stats["sells_removed"] += 1
            continue
        hold = _safe_float(s.get("hold_duration_hours"))
        if hold < 0.001 and (s.get("verified_onchain") or "").upper() != "TRUE":
            stats["sells_removed"] += 1
            continue
        profit = _safe_float(s.get("profit_percent"))
        if profit > cfg.MAX_REASONABLE_PROFIT:
            s["profit_percent"] = str(cfg.MAX_REASONABLE_PROFIT)
            stats["sells_capped"] += 1
        clean_s.append(s)
    db.write_csv(cfg.SELLS_FILE, db.sell_headers(), clean_s)

    # trades: drop blacklist + empty contract, then dedupe open by wallet+contract
    trades = db.read_csv(cfg.TRADES_FILE, db.trade_headers())
    step1 = []
    for t in trades:
        addr = (t.get("wallet_address") or "").lower()
        if db.is_blacklisted(addr):
            stats["trades_removed"] += 1
            continue
        contract = (t.get("contract") or "").strip().lower()
        if not contract or not contract.startswith("0x") or len(contract) < 10:
            stats["trades_no_contract"] += 1
            continue
        t["contract"] = contract
        t["wallet_address"] = addr
        step1.append(t)

    # keep newest open per (wallet, contract); keep all closed/partial as-is but still one open max
    from collections import defaultdict
    best_open = {}  # (wallet, contract) -> trade with latest buy_date
    others = []
    for t in step1:
        status = (t.get("status") or "").strip()
        key = (t.get("wallet_address", ""), t.get("contract", ""))
        if status in ("open", "partially_sold"):
            prev = best_open.get(key)
            if prev is None:
                best_open[key] = t
            else:
                d_new = t.get("buy_date") or ""
                d_old = prev.get("buy_date") or ""
                if d_new >= d_old:
                    others.append(prev)  # will count as dup; actually we discard old
                    stats["trades_dup_open"] += 1
                    best_open[key] = t
                else:
                    stats["trades_dup_open"] += 1
                    # keep prev, drop t
        else:
            others.append(t)

    # others may still contain older opens we marked — filter those out
    open_keys = set(best_open.keys())
    final = list(best_open.values())
    for t in others:
        status = (t.get("status") or "").strip()
        key = (t.get("wallet_address", ""), t.get("contract", ""))
        if status in ("open", "partially_sold") and key in open_keys:
            continue  # duplicate open already handled
        final.append(t)

    db.write_csv(cfg.TRADES_FILE, db.trade_headers(), final)
    stats["trades_removed"] += stats["trades_no_contract"] + stats["trades_dup_open"]

    logger.info("Sanitize done: %s", stats)
    return stats


def qualifies_as_whale(wallet: Dict) -> bool:
    """Check whale promotion rules."""
    wins = _safe_int(wallet.get("winning_sells"))
    win_rate = _safe_float(wallet.get("win_rate"))
    score = _safe_float(wallet.get("score"))
    trades = _safe_int(wallet.get("total_trades"))

    if wins < cfg.WHALE_MIN_WINNING_SELLS:
        return False
    if win_rate < cfg.WHALE_MIN_WIN_RATE:
        return False
    if score < cfg.WHALE_MIN_SCORE:
        return False
    if trades < cfg.WHALE_MIN_TRADES:
        return False

    last = wallet.get("last_seen") or ""
    if last:
        try:
            dt = datetime.fromisoformat(last.replace("Z", "+00:00").split("+")[0])
            age = (datetime.now(timezone.utc).replace(tzinfo=None) - dt).days
            if age > cfg.WHALE_MAX_INACTIVE_DAYS:
                return False
        except Exception:
            pass
    return True


def promote_whales() -> List[Dict]:
    """
    Promote qualifying wallets to whales.csv.
    Returns list of NEWLY promoted whales (for Telegram alerts).
    """
    wallets = db.read_csv(cfg.WALLETS_FILE, db.wallet_headers())
    existing = db.get_whale_addresses()
    newly = []

    for w in wallets:
        addr = (w.get("address") or "").lower()
        if not addr or addr in existing:
            # still refresh metrics on existing whales
            if addr in existing:
                db.upsert_whale({
                    "address": addr,
                    "chain": w.get("chain") or "ethereum",
                    "score": w.get("score") or "0",
                    "win_rate": w.get("win_rate") or "0",
                    "winning_sells": w.get("winning_sells") or "0",
                    "total_trades": w.get("total_trades") or "0",
                    "avg_profit": w.get("avg_profit") or "0",
                    "status": "active",
                })
                w["is_whale"] = "TRUE"
            continue

        if qualifies_as_whale(w):
            now = db.now_iso()
            db.upsert_whale({
                "address": addr,
                "chain": w.get("chain") or "ethereum",
                "promoted_at": now,
                "last_checked": now,
                "score": w.get("score") or "0",
                "win_rate": w.get("win_rate") or "0",
                "winning_sells": w.get("winning_sells") or "0",
                "total_trades": w.get("total_trades") or "0",
                "avg_profit": w.get("avg_profit") or "0",
                "status": "active",
            })
            w["is_whale"] = "TRUE"
            newly.append(dict(w))
            logger.info("🐋 Promoted to whale: %s (score=%s wins=%s)", addr[:12], w.get("score"), w.get("winning_sells"))
        else:
            w["is_whale"] = "FALSE"

    db.write_csv(cfg.WALLETS_FILE, db.wallet_headers(), wallets)
    logger.info("Whale promotion done. New whales: %d | Total active: %d", len(newly), len(db.get_whale_addresses()))
    return newly
