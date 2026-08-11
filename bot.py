#!/usr/bin/env python3
"""
Crypto Wallet Bot – main scanner.
Finds trending tokens, early buyers, records trades, sends Telegram signals.
"""
import logging
import sys
import time
from datetime import datetime, timezone

import config as cfg
import db
import apis
import telegram_utils as tg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bot")


def validate_env() -> bool:
    ok = True
    if not cfg.TELEGRAM_TOKEN or not cfg.CHAT_ID:
        logger.warning("TELEGRAM_TOKEN / CHAT_ID missing – signals will be dry-run only")
    if not cfg.ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY is required")
        ok = False
    if not cfg.BSCSCAN_API_KEY:
        logger.warning("BSCSCAN_API_KEY missing – BSC chain will be skipped or limited")
    return ok


def process_token(token: dict) -> tuple:
    """
    Find early buyers, record trades.
    Returns (buyers_list, new_trade_count).
    """
    contract = token.get("contract") or ""
    chain = (token.get("chain") or "").lower()
    if not contract or len(contract) < 10:
        return [], 0

    buyers = apis.find_early_buyers(contract, chain)
    if not buyers:
        logger.info("  no early buyers for %s", token.get("symbol"))
        return [], 0

    new_count = 0
    recorded_buyers = []
    for b in buyers:
        addr = b["address"]
        if db.is_blacklisted(addr):
            continue
        trade_id = db.add_trade(
            wallet_address=addr,
            token_info=token,
            price=float(token.get("price") or 0),
            chain=chain,
        )
        if trade_id:
            new_count += 1
            recorded_buyers.append(b)
            logger.info("  trade recorded: %s… for %s", addr[:10], token.get("symbol"))

    return recorded_buyers or buyers, new_count


def main() -> int:
    print("=" * 60)
    print(f"🚀 Scan started {datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 60)

    if not validate_env():
        logger.error("Environment validation failed")
        return 1

    db.ensure_data_dir()
    start = time.time()

    gainers = apis.fetch_gainers()
    if not gainers:
        logger.info("No quality gainers found this run")
        return 0

    logger.info("Top gainers to inspect: %d", min(cfg.MAX_TOKENS_TO_CHECK, len(gainers)))

    valid = []
    new_wallets = 0
    for token in gainers[: cfg.MAX_TOKENS_TO_CHECK]:
        symbol = token.get("symbol", "?")
        chain = token.get("chain", "?")
        logger.info("Checking %s on %s (Δ24h=%.1f%%)", symbol, chain, token.get("change_24h", 0))
        buyers, added = process_token(token)
        new_wallets += added
        if buyers:
            valid.append((token, buyers))
            logger.info("  → %d early buyers", len(buyers))

    whitelist = db.get_whitelist_addresses()
    logger.info(
        "Summary: quality=%d | with_buyers=%d | new_trades=%d | whitelist=%d",
        len(gainers), len(valid), new_wallets, len(whitelist),
    )

    # Send signals
    report_n = min(cfg.REPORT_COUNT, len(valid))
    for token, buyers in valid[:report_n]:
        is_wl = any(b.get("address", "").lower() in whitelist for b in buyers)
        msg = tg.format_signal(token, buyers, is_wl)
        tg.send_message(msg)
        time.sleep(2.5)

    if valid:
        tg.send_message(tg.format_performance({
            "total_tokens": len(gainers),
            "valid_tokens": len(valid),
            "new_wallets": new_wallets,
            "whitelist_count": len(whitelist),
        }))

    elapsed = time.time() - start
    print(f"✅ Done in {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
