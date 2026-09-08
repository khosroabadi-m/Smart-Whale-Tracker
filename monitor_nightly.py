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
from typing import Dict, List, Any, Optional

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


def _heal_nightly_log_header(path: str, headers: List[str]) -> None:
    """
    Rewrite nightly_log.csv if its header was written by an older schema
    (e.g. missing no_sell_after_buy). Rows are realigned positionally:
    current-width rows map onto the current headers, rows one field short
    belong to the old schema (same order, without no_sell_after_buy).
    Malformed rows of any other width are dropped.
    """
    with open(path, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    if not rows or rows[0] == headers:
        return
    logger.warning(
        "nightly_log.csv header mismatch (%d cols vs current %d) — rewriting",
        len(rows[0]), len(headers),
    )
    old_headers = [h for h in headers if h != "no_sell_after_buy"]
    aligned: List[Dict[str, Any]] = []
    for r in rows[1:]:
        if not r:
            continue
        if len(r) == len(headers):
            aligned.append(dict(zip(headers, r)))
        elif len(r) == len(old_headers):
            d = dict(zip(old_headers, r))
            d["no_sell_after_buy"] = ""
            aligned.append(d)
    db.write_csv(path, headers, aligned)


def append_nightly_log(row: Dict[str, Any]) -> None:
    db.ensure_data_dir()
    path = cfg.NIGHTLY_LOG_FILE
    headers = _nightly_log_headers()
    exists = os.path.exists(path) and os.path.getsize(path) > 0
    # also write per-run jsonl under data/logs for detail
    os.makedirs(cfg.LOGS_DIR, exist_ok=True)
    if exists:
        _heal_nightly_log_header(path, headers)
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


def _reply_to_source(row: Dict[str, Any]) -> Optional[int]:
    """Last Telegram message id stored on a wallet/whale row (thread source)."""
    try:
        mid = int(row.get("tg_message_id") or 0)
        return mid if mid > 0 else None
    except (TypeError, ValueError):
        return None


def _remember_message(row: Dict[str, Any], mid: Optional[int]) -> None:
    """Store the sent message id back on the wallet (and whale, if promoted)
    rows so the next event for the same address replies to it."""
    if not mid:
        return
    addr = (row.get("address") or "").lower()
    if not addr:
        return
    db.upsert_wallet({"address": addr, "tg_message_id": str(mid)})
    if db.is_whale(addr):
        db.upsert_whale({"address": addr, "tg_message_id": str(mid)})


def group_whale_events(events: List[Dict]) -> Dict[tuple, List[Dict]]:
    """
    Group buy/sell events by (type, contract) so each token gets ONE summary
    message per run instead of one message per transaction.
    """
    groups: Dict[tuple, List[Dict]] = {}
    for ev in events:
        etype = ev.get("type") or ""
        contract = (ev.get("contract") or "").lower()
        groups.setdefault((etype, contract), []).append(ev)
    return groups


def _open_trade_profit(addr: str, contract: str, price: float) -> Optional[float]:
    """Estimated profit % vs the recorded buy price of the open trade, if any."""
    if price <= 0:
        return None
    for t in db.get_open_trades():
        if (t.get("wallet_address") or "").lower() != addr:
            continue
        if (t.get("contract") or "").lower() != (contract or "").lower():
            continue
        try:
            bp = float(t.get("buy_price") or 0)
            if bp > 0:
                return ((price - bp) / bp) * 100.0
        except Exception:
            pass
        break
    return None


def _process_whale_events_aggregated(
    w: Dict, addr: str, chain: str, events: List[Dict],
) -> int:
    """
    Aggregated mode: ONE Telegram message per (token, buy|sell) group.
    Every tx is still recorded in whale_alerts.csv (dedupe stays per tx_hash),
    only the individual messages are suppressed.
    """
    alerted = 0
    for (etype, contract), group in group_whale_events(events).items():
        ev0 = group[0]
        token = ev0.get("token_symbol", "?")
        total_amount = sum(float(e.get("amount") or 0) for e in group)
        first_ts = min(int(e.get("timestamp") or 0) for e in group)
        last_ts = max(int(e.get("timestamp") or 0) for e in group)

        price = 0.0
        if contract:
            p = apis.get_token_price(contract, chain)
            if p:
                price = p

        for e in group:
            db.add_alert(
                etype, addr, e.get("token_symbol", ""), contract,
                chain, e.get("amount", 0), price, e.get("hash", ""),
                notes="aggregated",
            )

        if etype == "buy":
            db.add_trade(
                wallet_address=addr,
                token_info={
                    "symbol": token,
                    "name": ev0.get("token_name", "?"),
                    "contract": contract,
                },
                price=price or 0.0,
                chain=chain,
            )
            msg = tg.format_whale_buy_summary(
                w, token, chain, len(group), total_amount,
                price=price, first_ts=first_ts, last_ts=last_ts,
            )
        else:  # sell
            profit = _open_trade_profit(addr, contract, price)
            msg = tg.format_whale_sell_summary(
                w, token, chain, len(group), total_amount,
                price=price, profit_pct=profit,
                first_ts=first_ts, last_ts=last_ts,
            )

        mid = tg.send_message(msg, reply_to_message_id=_reply_to_source(w))
        _remember_message(w, mid)
        alerted += 1
        time.sleep(2.0)
    return alerted


def _process_whale_events_individual(
    w: Dict, addr: str, chain: str, events: List[Dict],
) -> int:
    """Legacy mode: one Telegram message per event."""
    alerted = 0
    for ev in events:
        price = 0.0
        if ev.get("contract"):
            p = apis.get_token_price(ev["contract"], chain)
            if p:
                price = p

        if ev["type"] == "buy":
            msg = tg.format_whale_buy(w, ev, price=price)
            mid = tg.send_message(msg, reply_to_message_id=_reply_to_source(w))
            _remember_message(w, mid)
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
            alerted += 1
            time.sleep(2.0)

        elif ev["type"] == "sell":
            profit = _open_trade_profit(addr, (ev.get("contract") or "").lower(), price)
            msg = tg.format_whale_sell(w, ev, price=price, profit_pct=profit)
            mid = tg.send_message(msg, reply_to_message_id=_reply_to_source(w))
            _remember_message(w, mid)
            db.add_alert(
                "sell", addr, ev.get("token_symbol", ""), ev.get("contract", ""),
                chain, ev.get("amount", 0), price, ev.get("hash", ""),
                notes=f"profit={profit}",
            )
            alerted += 1
            time.sleep(2.0)
    return alerted


def monitor_whales() -> int:
    """Scan active whales for recent buy/sell and alert.

    The scan window is exactly [last_checked → now] (clamped to
    WHALE_LOOKBACK_HOURS for whales not seen recently), i.e. only events
    between the previous run and this one.
    """
    whales = db.get_whales()
    active = [w for w in whales if (w.get("status") or "active") == "active"]
    active = active[: cfg.WHALE_MONITOR_MAX]
    if not active:
        logger.info("No whales to monitor yet")
        return 0

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    default_since = int((now - timedelta(hours=cfg.WHALE_LOOKBACK_HOURS)).timestamp())
    events_alerted = 0

    aggregate = getattr(cfg, "WHALE_AGGREGATE_EVENTS", True)
    logger.info("Monitoring %d whales (mode=%s)…", len(active),
                "aggregated" if aggregate else "individual")

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
        new_events = [
            ev for ev in events
            if not db.alert_exists(addr, ev.get("hash") or "")
        ]
        if new_events:
            if aggregate:
                events_alerted += _process_whale_events_aggregated(w, addr, chain, new_events)
            else:
                events_alerted += _process_whale_events_individual(w, addr, chain, new_events)

        db.update_whale_last_checked(addr)
        time.sleep(0.3)

    logger.info("Whale alert messages sent: %d", events_alerted)
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

        # STEP 1: Backfill BUYS — find ALL contracts the wallet bought (even unsold ones).
        # This creates new trade entries for contracts we didn't track before.
        # This is what increases `total_trades` count for whale qualification.
        try:
            buys = apis.backfill_wallet_buys(
                wallet=addr,
                chain=chain,
                days_back=cfg.BACKFILL_DAYS,
                max_tokens=cfg.BACKFILL_MAX_TOKENS_PER_WALLET,
            )
        except Exception as e:
            logger.warning("Backfill buys error for %s: %s", addr[:10], e)
            buys = []

        # Create trades for any contracts we don't already have a trade for
        all_trades = db.read_csv(cfg.TRADES_FILE, db.trade_headers())
        new_trades_created = 0
        for buy in buys:
            contract = (buy.get("contract") or "").lower()
            if not contract or not contract.startswith("0x"):
                continue
            # Check if we already have a trade for this (wallet, contract)
            already_tracked = any(
                (t.get("wallet_address") or "").lower() == addr and
                (t.get("contract") or "").lower() == contract
                for t in all_trades
            )
            if already_tracked:
                continue

            # Get buy price (try historical, fallback to current)
            buy_ts = buy.get("buy_timestamp") or 0
            est_buy_price = None
            if buy_ts > 0:
                est_buy_price = apis.get_token_price_at_timestamp(contract, chain, buy_ts)
            if not est_buy_price or est_buy_price <= 0:
                est_buy_price = apis.get_token_price(contract, chain)
            if not est_buy_price or est_buy_price <= 0:
                logger.info(
                    "Backfill %s: no price for %s, skip trade creation",
                    addr[:10], buy.get("token_symbol", "?"),
                )
                continue

            # Create the trade
            synthetic_token_info = {
                "symbol": buy.get("token_symbol") or "UNKNOWN",
                "name": buy.get("token_symbol") or "Backfilled",
                "contract": contract,
            }
            trade_id = db.add_trade(
                wallet_address=addr,
                token_info=synthetic_token_info,
                price=est_buy_price,
                chain=chain,
            )
            if trade_id:
                new_trades_created += 1
                logger.info(
                    "Backfill %s: created trade for %s (buy_price=%.8g)",
                    addr[:10], buy.get("token_symbol", "?"), est_buy_price,
                )
                # Refresh all_trades so next iteration sees it
                all_trades = db.read_csv(cfg.TRADES_FILE, db.trade_headers())
            time.sleep(0.2)

        if new_trades_created > 0:
            logger.info(
                "Backfill %s: created %d new trades (total_trades will increase)",
                addr[:10], new_trades_created,
            )

        # STEP 2: Backfill SELLS — find contracts where wallet both bought AND sold.
        # This records the actual sell events (with profit calculation).
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

            # Initialize historical price estimates (used in both branches below)
            buy_ts = s.get("buy_timestamp") or 0
            sell_ts = s.get("sell_timestamp") or 0
            est_buy_price = None
            est_sell_price = None
            if buy_ts > 0:
                est_buy_price = apis.get_token_price_at_timestamp(contract, chain, buy_ts)
            if sell_ts > 0:
                est_sell_price = apis.get_token_price_at_timestamp(contract, chain, sell_ts)

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
                # Determine buy_price: prefer historical estimate, fallback to current price
                if est_buy_price and est_buy_price > 0:
                    buy_price = est_buy_price
                else:
                    # No historical price — use current price (profit will be ~0%)
                    # This still creates a trade entry, which helps with WHALE_MIN_TRADES
                    buy_price = current_price
                    logger.info(
                        "Backfill %s: no historical buy price for %s, using current price (profit will be ~0%%)",
                        addr[:10], s.get("token_symbol", "?"),
                    )

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
                    # add_trade may have rejected if (wallet,contract) already open.
                    # Find it again in all_trades.
                    for t in all_trades:
                        if (t.get("wallet_address") or "").lower() == addr and \
                           (t.get("contract") or "").lower() == contract:
                            trade_id = t.get("trade_id")
                            break
                if not trade_id:
                    stats["skipped"] += 1
                    continue

            # Calculate profit using the best available prices
            if est_sell_price and est_sell_price > 0:
                # Use historical sell price if we have it
                profit = ((est_sell_price - buy_price) / buy_price) * 100.0
            else:
                # Fallback to current price for profit calculation
                profit = ((current_price - buy_price) / buy_price) * 100.0
            # NOTE: When we have historical prices, profit reflects actual sell-time profit.
            # When we don't, profit is ~0% (buy_price = current_price fallback).

            # Determine which sell_price to record
            sell_price_to_record = est_sell_price if (est_sell_price and est_sell_price > 0) else current_price

            # LIGHT dedup: only skip if EXACT same (wallet, contract, sell_price, profit) exists.
            # This is now handled by db.add_sell() internally, so we don't need to pre-check here.
            # The add_sell() function will return None if it's a genuine duplicate.

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
                sell_price=sell_price_to_record,
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
        if not msg:
            continue
        mid = tg.send_message(msg, reply_to_message_id=_reply_to_source(c))
        # record the alert only when Telegram actually accepted the message,
        # so a failed send is retried on the next run
        if mid:
            db.add_alert(
                "candidate", addr, "", "", c.get("chain", "ethereum"),
                0, 0, synth_hash, notes="whale candidate (≥1 verified winning sell)",
            )
            _remember_message(c, mid)
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
        # the promotion message threads onto the wallet's previous message
        # (e.g. its candidate alert) via tg_message_id
        mid = tg.send_message(tg.format_whale_promoted(w), reply_to_message_id=_reply_to_source(w))
        _remember_message(w, mid)
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

    # the nightly report continues its own thread: each report replies to the
    # previous one so the whole history stays linked in the chat
    tg.send_threaded(tg.format_nightly_report({
        "total_wallets": len(wallets),
        "new_sells": new_sells,
        "total_whales": len([w for w in whales if (w.get("status") or "active") == "active"]),
        "total_whitelist": len(whitelist),
    }, top, new_whales=len(newly), whale_events=whale_events,
       backfill_sells=backfill_stats.get("sells_recorded", 0),
       candidate_alerts=candidate_alerts,
       candidates=candidates if send_weekly else None), "nightly_report")

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
