#!/usr/bin/env python3
"""
One-shot / manual cleanup of historical CSV data + rescore.
"""
import logging
import sys

import scoring
import db
import config as cfg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fix_data")


def main() -> int:
    print("=" * 60)
    print("🛠️  Fix historical data")
    print("=" * 60)
    db.ensure_data_dir()

    stats = scoring.sanitize_existing_data()
    print(f"Sanitize: {stats}")

    scoring.update_all_scores()
    scoring.rebuild_whitelist()

    wallets = db.read_csv(cfg.WALLETS_FILE, db.wallet_headers())
    sells = db.read_csv(cfg.SELLS_FILE, db.sell_headers())
    wl = db.read_csv(cfg.WHITELIST_FILE, db.whitelist_headers())
    print(f"Wallets: {len(wallets)} | Sells: {len(sells)} | Whitelist: {len(wl)}")
    print("✅ Done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
