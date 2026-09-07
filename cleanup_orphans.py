#!/usr/bin/env python3
"""
One-time data cleanup:

1. Remove ORPHAN sells — sell rows whose trade_id no longer exists in
   trades.csv (leftovers from historic dedup/rewrite; they pollute wallet
   win counters while being untraceable to any trade).
2. Remove STALE zero-trade wallets — wallets with no trades AND no sells
   that are neither whales nor whitelisted.
3. Recompute total_trades per wallet from trades.csv, then rescore every
   wallet (scoring.update_all_scores) and rebuild the whitelist, so all
   counters match the remaining data exactly.

Idempotent: running it again on clean data removes nothing.
"""
import logging
import sys
from collections import Counter

import config as cfg
import db
import scoring

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("cleanup")


def main() -> int:
    trades = db.read_csv(cfg.TRADES_FILE, db.trade_headers())
    sells = db.read_csv(cfg.SELLS_FILE, db.sell_headers())
    wallets = db.read_csv(cfg.WALLETS_FILE, db.wallet_headers())
    whales = db.get_whale_addresses()
    whitelist = db.get_whitelist_addresses()

    # ---- 1) orphan sells ----
    trade_ids = {t.get("trade_id") for t in trades}
    orphan = [s for s in sells if (s.get("trade_id") or "") not in trade_ids]
    if orphan:
        kept = [s for s in sells if (s.get("trade_id") or "") in trade_ids]
        db.write_csv(cfg.SELLS_FILE, db.sell_headers(), kept)
    logger.info(
        "Orphan sells removed: %d (sells %d -> %d)",
        len(orphan), len(sells), len(sells) - len(orphan),
    )

    # ---- 2) stale zero-trade wallets ----
    wallet_trades = {(t.get("wallet_address") or "").lower() for t in trades}
    wallet_sells = {(s.get("wallet_address") or "").lower() for s in sells}
    stale_addrs = set()
    for w in wallets:
        addr = (w.get("address") or "").lower()
        if addr in wallet_trades or addr in wallet_sells:
            continue
        if addr in whales or addr in whitelist:
            continue
        stale_addrs.add(addr)
    if stale_addrs:
        kept_w = [
            w for w in wallets
            if (w.get("address") or "").lower() not in stale_addrs
        ]
        db.write_csv(cfg.WALLETS_FILE, db.wallet_headers(), kept_w)
    logger.info(
        "Stale zero-trade wallets removed: %d (wallets %d -> %d)",
        len(stale_addrs), len(wallets), len(wallets) - len(stale_addrs),
    )

    # ---- 3) resync counters + rescore ----
    wallets = db.read_csv(cfg.WALLETS_FILE, db.wallet_headers())
    tcount = Counter((t.get("wallet_address") or "").lower() for t in trades)
    for w in wallets:
        w["total_trades"] = str(tcount.get((w.get("address") or "").lower(), 0))
    db.write_csv(cfg.WALLETS_FILE, db.wallet_headers(), wallets)

    scoring.update_all_scores()
    scoring.rebuild_whitelist()
    logger.info("Rescored %d wallets and rebuilt whitelist", len(wallets))
    return 0


if __name__ == "__main__":
    sys.exit(main())
