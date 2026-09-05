#!/usr/bin/env python3
"""
Nightly job:
1. Detect on-chain sells for open trades (with contract only)
2. Recalculate scores
3. Promote whales
4. Monitor whale buy/sell activity
5. Structured nightly log (retained ~30 days)
6. Send detailed Telegram reports
"""
import csv
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any

import config as cfg
import db
import apis
import scoring
import telegram_utils as tg
import version

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("nightly")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00").split("+")[0])


# -------------------- Nightly structured log --------------------

def _nightly_log_headers() -> List[str]:
    return [
        "run_id", "started_at", "finished_at",
        "open_with_contract", "checked", "skipped_no_contract",
        "sell_detected", "sell_recorded", "sell_no_price", "sell_below_threshold",
        "api_empty_transfers", "no_sell_after_buy", "errors",
        "new_whales", "whale_events", "notes",
    ]


def append_nightly_log(row: Dict[str, Any]) -> None:
    db.ensure_data_dir()
    path = cfg.NIGHTLY_LOG_FILE
    headers = _nightly_log_headers()
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    # also write per-run jsonl under data/logs for detail
    os.makedirs(cfg.LOGS_DIR, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        if not exists:
            w.writeheader()
        clean = {h: row.get(h, "") for h in headers}
        w.writerow(clean)


def cleanup_old_logs() -> int:
    """Delete nightly detail files older than retention; trim CSV by date."""
    removed = 0
    retention = getattr(cfg, "NIGHTLY_LOG_RETENTION_DAYS", 30)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=retention)

    logs_dir = getattr(cfg, "LOGS_DIR", os.path.join(cfg.DATA_DIR, "logs"))
    if os.path.isdir(logs_dir):
        for name in os.listdir(logs_dir):
            fp = os.path.join(logs_dir, name)
            if not os.path.isfile(fp):
                continue
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(fp))
                if mtime < cutoff:
                    os.unlink(fp)
                    removed += 1
            except Exception:
                continue

    # trim aggregate CSV
    path = cfg.NIGHTLY_LOG_FILE
    if os.path.exists(path):
        rows = db.read_csv(path, _nightly_log_headers())
        kept = []
        for r in rows:
            try:
                dt = _parse_iso(r.get("started_at") or "")
                if dt >= cutoff:
                    kept.append(r)
            except Exception:
                kept.append(r)
        if len(kept) != len(rows):
            db.write_csv(path, _nightly_log_headers(), kept)
            removed += len(rows) - len(kept)
    return removed


def write_run_detail(run_id: str, lines: List[str]) -> None:
    os.makedirs(cfg.LOGS_DIR, exist_ok=True)
    fp = os.path.join(cfg.LOGS_DIR, f"nightly_{run_id}.log")
    with open(fp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# -------------------- Sell scan --------------------

def process_open_trades(max_trades: int = None) -> Dict[str, int]:
    """
    Only check OPEN trades that have a real contract.
    Oldest first. Returns counters for logging.
    """
    if max_trades is None:
        # FIX: was getattr(cfg, "MAX_TRADES_PER_NIGHTLY", 80) — but cfg has it =500
        # so default never applied, but config is correctly 500. Keep direct access.
        max_trades = cfg.MAX_TRADES_PER_NIGHTLY

    open_trades = db.get_open_trades()
    with_c = [
        t for t in open_trades
        if (t.get("contract") or "").strip().startswith("0x")
        and len((t.get("contract") or "").strip()) >= 10
    ]
    without = len(open_trades) - len(with_c)

    with_c.sort(key=lambda t: t.get("buy_date") or "")
    ordered = with_c[:max_trades]

    counters = {
        "open_with_contract": len(with_c),
        "checked": 0,
        "skipped_no_contract": without,
        "sell_detected": 0,
        "sell_recorded": 0,
        "sell_no_price": 0,
        "sell_below_threshold": 0,
        "api_empty_transfers": 0,
        "no_sell_after_buy": 0,
        "errors": 0,
    }
    detail: List[str] = []

    logger.info(
        "Checking %d/%d open trades with contract (skipped_no_contract=%d)…",
        len(ordered), len(with_c), without,
    )
    detail.append(f"queue={len(ordered)} with_contract_total={len(with_c)} legacy_skipped={without}")

    for trade in ordered:
        counters["checked"] += 1
        wallet = (trade.get("wallet_address") or "").lower()
        contract = (trade.get("contract") or "").strip().lower()
        chain = (trade.get("chain") or "ethereum").lower()
        token = trade.get("token") or ""
        trade_id = trade.get("trade_id") or ""
        try:
            buy_price = float(trade.get("buy_price") or 0)
        except Exception:
            buy_price = 0.0

        if not wallet or not contract or buy_price <= 0:
            counters["errors"] += 1
            continue
        if db.is_blacklisted(wallet):
            continue

        try:
            buy_dt = _parse_iso(trade["buy_date"])
            buy_ts = int(buy_dt.timestamp())
        except Exception:
            counters["errors"] += 1
            continue

        hold_hours = (datetime.now(timezone.utc).replace(tzinfo=None) - buy_dt).total_seconds() / 3600.0
        if hold_hours < cfg.MIN_HOLD_HOURS:
            continue

        try:
            sell_info = apis.detect_onchain_sell(wallet, contract, chain, buy_ts)
        except Exception as e:
            counters["errors"] += 1
            detail.append(f"ERR detect {token} {wallet[:10]}: {e}")
            continue

        if not sell_info or sell_info.get("_status") == "empty_api":
            counters["api_empty_transfers"] += 1
            continue

        if sell_info.get("_status") == "no_sell":
            counters["no_sell_after_buy"] += 1
            continue

        if sell_info.get("sold_percent", 0) < 10:
            counters["sell_below_threshold"] += 1
            detail.append(
                f"below10% {token} {wallet[:10]} sold={sell_info.get('sold_percent'):.1f}"
            )
            continue

        counters["sell_detected"] += 1
        current_price = apis.get_token_price(contract, chain)
        if current_price is None or current_price <= 0:
            counters["sell_no_price"] += 1
            logger.info("Sell detected but no price for %s – skip", token)
            detail.append(f"no_price {token} {wallet[:10]}")
            continue

        profit = ((current_price - buy_price) / buy_price) * 100.0
        if profit >= cfg.MIN_PROFIT_FOR_WIN:
            is_winning = True
        elif profit <= cfg.MIN_LOSS_FOR_LOSS:
            is_winning = False
        else:
            is_winning = profit > 0

        sid = db.add_sell(
            trade_id=trade_id,
            wallet_address=wallet,
            token=token,
            contract=contract,
            sell_price=current_price,
            sell_percent=min(100.0, float(sell_info["sold_percent"])),
            profit_percent=profit,
            is_winning=is_winning,
            hold_duration=hold_hours,
            verified_onchain=True,
        )
        if sid:
            counters["sell_recorded"] += 1
            msg = (
                f"SELL {token} {wallet[:10]} profit={profit:.1f}% "
                f"hold={hold_hours:.1f}h sold={sell_info['sold_percent']:.1f}%"
            )
            logger.info("On-chain sell: %s", msg)
            detail.append(msg)
        time.sleep(0.2)

    counters["_detail"] = detail
    return counters


def monitor_whales() -> int:
    """Scan active whales for recent buy/sell and alert."""
    whales = db.get_whales()
    active = [w for w in whales if (w.get("status") or "active") == "active"]
    active = active[: cfg.WHALE_MONITOR_MAX]
    if not active:
        logger.info("No whales to monitor yet")
        return 0

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    default_since = int((now - timedelta(hours=cfg.WHALE_LOOKBACK_HOURS)).timestamp())
    events_alerted = 0

    logger.info("Monitoring %d whales…", len(active))
    for w in active:
        addr = (w.get("address") or "").lower()
        chain = (w.get("chain") or "ethereum").lower()
        if not addr:
            continue

        last_checked = w.get("last_checked") or ""
        try:
            since_ts = int(_parse_iso(last_checked).timestamp()) if last_checked else default_since
        except Exception:
            since_ts = default_since
        since_ts = max(since_ts, default_since)

        events = apis.parse_whale_activity(addr, chain, since_ts)
        for ev in events:
            if db.alert_exists(addr, ev.get("hash") or ""):
                continue

            price = 0.0
            if ev.get("contract"):
                p = apis.get_token_price(ev["contract"], chain)
                if p:
                    price = p

            if ev["type"] == "buy":
                msg = tg.format_whale_buy(w, ev, price=price)
                tg.send_message(msg)
                db.add_alert(
                    "buy", addr, ev.get("token_symbol", ""), ev.get("contract", ""),
                    chain, ev.get("amount", 0), price, ev.get("hash", ""),
                    notes="whale buy",
                )
                db.add_trade(
                    wallet_address=addr,
                    token_info={
                        "symbol": ev.get("token_symbol", "?"),
                        "name": ev.get("token_name", "?"),
                        "contract": ev.get("contract", ""),
                    },
                    price=price or 0.0,
                    chain=chain,
                )
                events_alerted += 1
                time.sleep(2.0)

            elif ev["type"] == "sell":
                profit = None
                for t in db.get_open_trades():
                    if (t.get("wallet_address") or "").lower() != addr:
                        continue
                    if (t.get("contract") or "").lower() != (ev.get("contract") or "").lower():
                        continue
                    try:
                        bp = float(t.get("buy_price") or 0)
                        if bp > 0 and price > 0:
                            profit = ((price - bp) / bp) * 100.0
                    except Exception:
                        pass
                    break

                msg = tg.format_whale_sell(w, ev, price=price, profit_pct=profit)
                tg.send_message(msg)
                db.add_alert(
                    "sell", addr, ev.get("token_symbol", ""), ev.get("contract", ""),
                    chain, ev.get("amount", 0), price, ev.get("hash", ""),
                    notes=f"profit={profit}",
                )
                events_alerted += 1
                time.sleep(2.0)

        db.update_whale_last_checked(addr)
        time.sleep(0.3)

    logger.info("Whale events alerted: %d", events_alerted)
    return events_alerted


# -------------------- Backfill candidates --------------------

def backfill_candidates() -> Dict[str, int]:
    """
    For each whale candidate (≥1 verified winning sell, not yet whale),
    look back N days into their on-chain history to find OTHER profitable
    sells we missed. This breaks the chicken-and-egg: most wallets had only
    1 trade recorded because we only saw them buy 1 trending token.

    Returns counters.
    """
    if not getattr(cfg, "BACKFILL_ENABLED", False):
        return {"wallets_backfilled": 0, "sells_found": 0, "sells_recorded": 0, "skipped": 0}

    candidates = scoring.get_whale_candidates(limit=cfg.BACKFILL_MAX_WALLETS_PER_RUN)
    if not candidates:
        logger.info("Backfill: no candidates this run")
        return {"wallets_backfilled": 0, "sells_found": 0, "sells_recorded": 0, "skipped": 0}

    stats = {"wallets_backfilled": 0, "sells_found": 0, "sells_recorded": 0, "skipped": 0}
    logger.info("Backfill: %d candidates to scan", len(candidates))

    for cand in candidates:
        addr = (cand.get("address") or "").lower()
        chain = (cand.get("chain") or "ethereum").lower()
        if not addr:
            continue

        try:
            found = apis.backfill_wallet_sells(
                wallet=addr,
                chain=chain,
                days_back=cfg.BACKFILL_DAYS,
                max_tokens=cfg.BACKFILL_MAX_TOKENS_PER_WALLET,
            )
        except Exception as e:
            logger.warning("Backfill error for %s: %s", addr[:10], e)
            continue

        stats["wallets_backfilled"] += 1
        stats["sells_found"] += len(found)

        if not found:
            logger.info("Backfill %s: API returned 0 sells (chain=%s)", addr[:10], chain)
            continue

        # Load all existing trades ONCE (not per-found-sell)
        all_trades = db.read_csv(cfg.TRADES_FILE, db.trade_headers())
        existing_sells = db.read_csv(cfg.SELLS_FILE, db.sell_headers())

        for s in found:
            contract = (s.get("contract") or "").lower()
            if not contract or not contract.startswith("0x"):
                stats["skipped"] += 1
                continue
            sell_ts = s.get("sell_timestamp") or 0
            buy_ts = s.get("buy_timestamp") or 0
            if sell_ts <= 0:
                stats["skipped"] += 1
                continue

            # Get current price (DexScreener → GeckoTerminal fallback)
            current_price = apis.get_token_price(contract, chain)
            if not current_price or current_price <= 0:
                logger.info(
                    "Backfill %s: no price for %s (%s), skip — DexScreener + GeckoTerminal both failed",
                    addr[:10], s.get("token_symbol", "?"), contract[:14],
                )
                stats["skipped"] += 1
                continue

            # Look for existing trade for this wallet+contract
            existing_trade = None
            for t in all_trades:
                if (t.get("wallet_address") or "").lower() == addr and \
                   (t.get("contract") or "").lower() == contract:
                    existing_trade = t
                    break

            if existing_trade:
                # We have a trade — use its buy_price
                trade_id = existing_trade.get("trade_id")
                buy_price = float(existing_trade.get("buy_price") or 0)
                if buy_price <= 0:
                    stats["skipped"] += 1
                    continue
            else:
                # No existing trade — CREATE a backfilled trade.
                # Try to estimate historical buy_price using DexScreener's 24h price change.
                # If we can't get a historical estimate, SKIP this sell (don't pollute
                # data with 0% profit synthetic trades — they're useless for whale qualification).
                buy_ts = s.get("buy_timestamp") or 0
                est_buy_price = None
                if buy_ts > 0:
                    est_buy_price = apis.get_token_price_at_timestamp(contract, chain, buy_ts)

                if est_buy_price and est_buy_price > 0:
                    # We have a historical price estimate — use it as buy_price
                    buy_price = est_buy_price
                    synthetic_token_info = {
                        "symbol": s.get("token_symbol") or "UNKNOWN",
                        "name": s.get("token_symbol") or "Backfilled",
                        "contract": contract,
                    }
                    trade_id = db.add_trade(
                        wallet_address=addr,
                        token_info=synthetic_token_info,
                        price=buy_price,
                        chain=chain,
                    )
                    if not trade_id:
                        for t in all_trades:
                            if (t.get("wallet_address") or "").lower() == addr and \
                               (t.get("contract") or "").lower() == contract:
                                trade_id = t.get("trade_id")
                                break
                    if not trade_id:
                        stats["skipped"] += 1
                        continue
                else:
                    # No historical price available — skip this sell.
                    # Creating a 0% profit synthetic trade would pollute the data.
                    logger.info(
                        "Backfill %s: no historical price for %s, skip (would be 0%% profit)",
                        addr[:10], s.get("token_symbol", "?"),
                    )
                    stats["skipped"] += 1
                    continue

            profit = ((current_price - buy_price) / buy_price) * 100.0
            # NOTE: this is the CURRENT profit, not the historical sell-time profit.

            # Skip if we already have a sell for this trade
            already = any(
                (es.get("trade_id") == trade_id and
                 es.get("wallet_address", "").lower() == addr)
                for es in existing_sells
            )
            if already:
                stats["skipped"] += 1
                continue

            # Compute hold_duration from buy_ts to sell_ts
            hold_hours = (sell_ts - buy_ts) / 3600.0 if buy_ts > 0 else 0.0
            if hold_hours < cfg.MIN_HOLD_HOURS:
                stats["skipped"] += 1
                continue

            is_winning = profit >= cfg.MIN_PROFIT_FOR_WIN
            sid = db.add_sell(
                trade_id=trade_id,
                wallet_address=addr,
                token=s.get("token_symbol") or "???",
                contract=contract,
                sell_price=current_price,
                sell_percent=s.get("sold_percent", 100.0),
                profit_percent=profit,
                is_winning=is_winning,
                hold_duration=hold_hours,
                verified_onchain=True,
            )
            if sid:
                stats["sells_recorded"] += 1
                logger.info(
                    "Backfilled sell: %s %s profit=%.1f%% hold=%.1fh sold=%.1f%%",
                    s.get("token_symbol"), addr[:10], profit, hold_hours,
                    s.get("sold_percent", 0),
                )
                # Refresh existing_sells so next iteration sees the new one
                existing_sells = db.read_csv(cfg.SELLS_FILE, db.sell_headers())
            time.sleep(0.3)

    logger.info(
        "Backfill done: wallets=%d, sells_found=%d, sells_recorded=%d, skipped=%d",
        stats["wallets_backfilled"], stats["sells_found"],
        stats["sells_recorded"], stats["skipped"],
    )
    return stats


# -------------------- Candidate alerts --------------------

def send_candidate_alerts() -> int:
    """
    Alert when a wallet FIRST reaches the candidate threshold (≥1 verified winning sell).
    Uses whale_alerts.csv to dedupe: candidate alerts use tx_hash='candidate_<addr>'.
    """
    if not getattr(cfg, "ALERT_CANDIDATE_ENABLED", True):
        return 0

    candidates = scoring.get_whale_candidates(limit=50)
    alerted = 0
    for c in candidates:
        addr = (c.get("address") or "").lower()
        if not addr:
            continue
        # Dedupe by a synthetic tx_hash so we don't alert the same wallet twice
        synth_hash = f"candidate_{addr[:12]}"
        if db.alert_exists(addr, synth_hash):
            continue

        msg = tg.format_whale_candidate(c)
        if msg and tg.send_message(msg):
            db.add_alert(
                "candidate", addr, "", "", c.get("chain", "ethereum"),
                0, 0, synth_hash, notes="whale candidate (≥1 verified winning sell)",
            )
            alerted += 1
            time.sleep(1.5)
    return alerted


def main() -> int:
    started = datetime.now(timezone.utc).replace(tzinfo=None)
    run_id = started.strftime("%Y%m%d_%H%M%S")
    print("=" * 60)
    print(f"🐋 {version.get_version_banner()} · Nightly started {started.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 60)

    db.ensure_data_dir()
    os.makedirs(cfg.LOGS_DIR, exist_ok=True)

    scoring.sanitize_existing_data()

    # Step 1: scan open trades for new on-chain sells
    sell_stats = process_open_trades()
    new_sells = sell_stats.get("sell_recorded", 0)
    logger.info(
        "New sells recorded: %d | detected=%d | no_price=%d | empty_api=%d | no_sell=%d",
        new_sells, sell_stats.get("sell_detected", 0),
        sell_stats.get("sell_no_price", 0), sell_stats.get("api_empty_transfers", 0),
        sell_stats.get("no_sell_after_buy", 0),
    )

    # Step 2: rescore wallets
    scoring.update_all_scores()
    scoring.cleanup_old_wallets()
    scoring.rebuild_whitelist()

    # Step 3: backfill — find missed sells in candidate wallet histories
    backfill_stats = backfill_candidates()
    if backfill_stats.get("sells_recorded", 0) > 0:
        # Re-score after backfill found new sells
        scoring.update_all_scores()
        scoring.rebuild_whitelist()

    # Step 4: promote new whales (may trigger due to backfill discoveries)
    newly = scoring.promote_whales()
    for w in newly:
        tg.send_message(tg.format_whale_promoted(w))
        db.add_alert(
            "promote", w.get("address", ""), "", "", w.get("chain", "ethereum"),
            0, 0, f"promote_{w.get('address', '')[:10]}", notes="promoted to whale",
        )
        time.sleep(2.0)

    # Step 5: alert on new candidates (≥1 verified winning sell, first time)
    candidate_alerts = send_candidate_alerts()

    # Step 6: monitor active whales for new buy/sell events
    whale_events = monitor_whales()

    wallets = db.read_csv(cfg.WALLETS_FILE, db.wallet_headers())
    whitelist = db.read_csv(cfg.WHITELIST_FILE, db.whitelist_headers())
    whales = db.get_whales()
    top = sorted(wallets, key=lambda x: float(x.get("score") or 0), reverse=True)[:5]
    candidates = scoring.get_whale_candidates(limit=5)

    # Weekly summary on a specific weekday (configurable)
    is_weekly_day = started.weekday() == getattr(cfg, "WEEKLY_SUMMARY_DAY", 6)
    send_weekly = getattr(cfg, "WEEKLY_SUMMARY_ENABLED", True) and is_weekly_day

    tg.send_message(tg.format_nightly_report({
        "total_wallets": len(wallets),
        "new_sells": new_sells,
        "total_whales": len([w for w in whales if (w.get("status") or "active") == "active"]),
        "total_whitelist": len(whitelist),
    }, top, new_whales=len(newly), whale_events=whale_events,
       backfill_sells=backfill_stats.get("sells_recorded", 0),
       candidate_alerts=candidate_alerts,
       candidates=candidates if send_weekly else None))

    finished = datetime.now(timezone.utc).replace(tzinfo=None)
    detail_lines = sell_stats.pop("_detail", [])
    write_run_detail(run_id, [
        f"run_id={run_id}",
        f"started={started.isoformat()}",
        f"finished={finished.isoformat()}",
        f"stats={sell_stats}",
        f"backfill={backfill_stats}",
        f"new_whales={len(newly)}",
        f"whale_events={whale_events}",
        f"candidate_alerts={candidate_alerts}",
        "--- detail ---",
        *detail_lines[:200],
    ])
    append_nightly_log({
        "run_id": run_id,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "open_with_contract": sell_stats.get("open_with_contract", 0),
        "checked": sell_stats.get("checked", 0),
        "skipped_no_contract": sell_stats.get("skipped_no_contract", 0),
        "sell_detected": sell_stats.get("sell_detected", 0),
        "sell_recorded": sell_stats.get("sell_recorded", 0),
        "sell_no_price": sell_stats.get("sell_no_price", 0),
        "sell_below_threshold": sell_stats.get("sell_below_threshold", 0),
        "api_empty_transfers": sell_stats.get("api_empty_transfers", 0),
        "no_sell_after_buy": sell_stats.get("no_sell_after_buy", 0),
        "errors": sell_stats.get("errors", 0),
        "new_whales": len(newly),
        "whale_events": whale_events,
        "notes": f"detail=data/logs/nightly_{run_id}.log | backfill={backfill_stats.get('sells_recorded',0)} | candidates={candidate_alerts}",
    })
    removed_logs = cleanup_old_logs()
    if removed_logs:
        logger.info("Cleaned %d old log entries/files", removed_logs)

    print("✅ Nightly finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
