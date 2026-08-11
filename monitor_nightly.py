#!/usr/bin/env python3
"""
Nightly job:
1. Try to detect real on-chain sells for open trades
2. Recalculate scores
3. Cleanup stale wallets
4. Rebuild whitelist
5. Send summary to Telegram
"""
import logging
import sys
import time
from datetime import datetime, timezone

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
    """
    For open trades, check on-chain if the wallet sold the token.
    Also fall back to price-based classification only when on-chain sell is confirmed.
    Returns number of new sells recorded.
    """
    open_trades = db.get_open_trades()
    # prefer older trades first
    open_trades.sort(key=lambda t: t.get("buy_date") or "")
    open_trades = open_trades[:max_trades]

    new_sells = 0
    logger.info("Checking %d open trades for sells…", len(open_trades))

    for trade in open_trades:
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

        # 1) On-chain detection
        sell_info = apis.detect_onchain_sell(wallet, contract, chain, buy_ts)
        if not sell_info or sell_info["sold_percent"] < 10:
            # no meaningful sell yet
            continue

        # 2) Price at detection time (best effort)
        current_price = apis.get_token_price(contract, chain)
        if current_price is None or current_price <= 0:
            # still record sell with unknown profit? skip profit classification
            logger.info("Sell detected but no price for %s – skip", token)
            continue

        profit = ((current_price - buy_price) / buy_price) * 100.0
        # classify
        if profit >= cfg.MIN_PROFIT_FOR_WIN:
            is_winning = True
        elif profit <= cfg.MIN_LOSS_FOR_LOSS:
            is_winning = False
        else:
            # neutral move – still record as non-winning
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


def main() -> int:
    print("=" * 60)
    print(f"🌙 Nightly started {datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 60)

    db.ensure_data_dir()

    # Optional light sanitize each night
    scoring.sanitize_existing_data()

    # Detect sells
    new_sells = process_open_trades()
    logger.info("New sells recorded: %d", new_sells)

    # Scores + whitelist
    scoring.update_all_scores()
    scoring.cleanup_old_wallets()
    scoring.rebuild_whitelist()

    # Stats for report
    wallets = db.read_csv(cfg.WALLETS_FILE, db.wallet_headers())
    whitelist = db.read_csv(cfg.WHITELIST_FILE, db.whitelist_headers())
    total_trades = sum(int(float(w.get("total_trades") or 0)) for w in wallets)
    total_sells = sum(int(float(w.get("total_sells") or 0)) for w in wallets)

    top = sorted(wallets, key=lambda x: float(x.get("score") or 0), reverse=True)[:5]

    tg.send_message(tg.format_nightly({
        "total_wallets": len(wallets),
        "total_whitelist": len(whitelist),
        "total_trades": total_trades,
        "total_sells": total_sells,
    }, top))

    print("✅ Nightly finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
