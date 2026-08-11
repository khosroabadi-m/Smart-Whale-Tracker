#!/usr/bin/env python3
"""
Nightly job:
1. Detect on-chain sells for open trades
2. Recalculate scores
3. Promote whales
4. Monitor whale buy/sell activity
5. Send detailed Telegram reports
"""
import logging
import sys
import time
from datetime import datetime, timezone, timedelta

import config as cfg
import db
import apis
import scoring
import telegram_utils as tg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("nightly")


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00").split("+")[0])


def process_open_trades(max_trades: int = 40) -> int:
    open_trades = db.get_open_trades()
    # prefer trades that HAVE contract (new system)
    with_c = [t for t in open_trades if (t.get("contract") or "").strip()]
    without = [t for t in open_trades if not (t.get("contract") or "").strip()]
    # process ones with contract first, then legacy limited
    ordered = with_c + without
    ordered.sort(key=lambda t: t.get("buy_date") or "")
    ordered = ordered[:max_trades]

    new_sells = 0
    logger.info("Checking %d open trades for sells (with_contract=%d)…", len(ordered), len(with_c))

    for trade in ordered:
        wallet = trade.get("wallet_address") or ""
        contract = trade.get("contract") or ""
        chain = trade.get("chain") or "ethereum"
        token = trade.get("token") or ""
        trade_id = trade.get("trade_id") or ""
        buy_price = float(trade.get("buy_price") or 0)

        if not wallet or not contract or buy_price <= 0:
            continue
        if db.is_blacklisted(wallet):
            continue

        try:
            buy_dt = _parse_iso(trade["buy_date"])
            buy_ts = int(buy_dt.timestamp())
        except Exception:
            continue

        hold_hours = (datetime.now(timezone.utc).replace(tzinfo=None) - buy_dt).total_seconds() / 3600.0
        if hold_hours < cfg.MIN_HOLD_HOURS:
            continue

        sell_info = apis.detect_onchain_sell(wallet, contract, chain, buy_ts)
        if not sell_info or sell_info["sold_percent"] < 10:
            continue

        current_price = apis.get_token_price(contract, chain)
        if current_price is None or current_price <= 0:
            logger.info("Sell detected but no price for %s – skip", token)
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
            sell_percent=min(100.0, sell_info["sold_percent"]),
            profit_percent=profit,
            is_winning=is_winning,
            hold_duration=hold_hours,
            verified_onchain=True,
        )
        if sid:
            new_sells += 1
            logger.info(
                "On-chain sell: %s %s profit=%.1f%% hold=%.1fh",
                token, wallet[:10], profit, hold_hours,
            )
        time.sleep(0.2)

    return new_sells


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
        # never look further than lookback
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
                db.add_alert("buy", addr, ev.get("token_symbol", ""), ev.get("contract", ""),
                             chain, ev.get("amount", 0), price, ev.get("hash", ""),
                             notes="whale buy")
                # also record as trade for future sell tracking
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
                # try match open trade for profit estimate
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
                db.add_alert("sell", addr, ev.get("token_symbol", ""), ev.get("contract", ""),
                             chain, ev.get("amount", 0), price, ev.get("hash", ""),
                             notes=f"profit={profit}")
                events_alerted += 1
                time.sleep(2.0)

        db.update_whale_last_checked(addr)
        time.sleep(0.3)

    logger.info("Whale events alerted: %d", events_alerted)
    return events_alerted


def main() -> int:
    print("=" * 60)
    print(f"🌙 Nightly started {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 60)

    db.ensure_data_dir()
    scoring.sanitize_existing_data()

    new_sells = process_open_trades()
    logger.info("New sells recorded: %d", new_sells)

    scoring.update_all_scores()
    scoring.cleanup_old_wallets()
    scoring.rebuild_whitelist()

    newly = scoring.promote_whales()
    for w in newly:
        tg.send_message(tg.format_whale_promoted(w))
        db.add_alert(
            "promote", w.get("address", ""), "", "", w.get("chain", "ethereum"),
            0, 0, f"promote_{w.get('address','')[:10]}", notes="promoted to whale",
        )
        time.sleep(2.0)

    whale_events = monitor_whales()

    wallets = db.read_csv(cfg.WALLETS_FILE, db.wallet_headers())
    whitelist = db.read_csv(cfg.WHITELIST_FILE, db.whitelist_headers())
    whales = db.get_whales()
    top = sorted(wallets, key=lambda x: float(x.get("score") or 0), reverse=True)[:5]

    tg.send_message(tg.format_nightly_report({
        "total_wallets": len(wallets),
        "new_sells": new_sells,
        "total_whales": len([w for w in whales if (w.get("status") or "active") == "active"]),
        "total_whitelist": len(whitelist),
    }, top, new_whales=len(newly), whale_events=whale_events))

    print("✅ Nightly finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
