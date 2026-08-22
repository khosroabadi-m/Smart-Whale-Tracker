#!/usr/bin/env python3
"""
One-shot / manual cleanup of historical CSV data + rescore.
Removes empty-contract trades and open duplicates; rebuilds scores.
"""
import logging
import sys
from collections import Counter

import scoring
import db
import config as cfg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fix_data")


def recompute_wallet_trade_counts() -> None:
    """After deleting trades, sync total_trades on wallets from remaining rows."""
    trades = db.read_csv(cfg.TRADES_FILE, db.trade_headers())
    counts = Counter((t.get("wallet_address") or "").lower() for t in trades if t.get("wallet_address"))
    wallets = db.read_csv(cfg.WALLETS_FILE, db.wallet_headers())
    for w in wallets:
        addr = (w.get("address") or "").lower()
        w["total_trades"] = str(counts.get(addr, 0))
    db.write_csv(cfg.WALLETS_FILE, db.wallet_headers(), wallets)
    logger.info("Recomputed total_trades for %d wallets", len(wallets))


def main() -> int:
    print("=" * 60)
    print("🛠️  Smart Whale Tracker · Fix historical data")
    print("=" * 60)
    db.ensure_data_dir()

    before_t = len(db.read_csv(cfg.TRADES_FILE, db.trade_headers()))
    stats = scoring.sanitize_existing_data()
    print(f"Sanitize: {stats}")

    recompute_wallet_trade_counts()
    scoring.update_all_scores()
    scoring.rebuild_whitelist()
    scoring.promote_whales()

    wallets = db.read_csv(cfg.WALLETS_FILE, db.wallet_headers())
    sells = db.read_csv(cfg.SELLS_FILE, db.sell_headers())
    wl = db.read_csv(cfg.WHITELIST_FILE, db.whitelist_headers())
    trades = db.read_csv(cfg.TRADES_FILE, db.trade_headers())
    after_t = len(trades)
    with_c = sum(1 for t in trades if (t.get("contract") or "").startswith("0x"))
    print(f"Trades: {before_t} → {after_t} (with_contract={with_c})")
    print(f"Wallets: {len(wallets)} | Sells: {len(sells)} | Whitelist: {len(wl)}")
    print("✅ Done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
