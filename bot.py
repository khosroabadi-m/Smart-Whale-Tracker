#!/usr/bin/env python3
"""Crypto Wallet Bot – discovery scanner."""
import logging
import sys
import time
from datetime import datetime, timezone

import config as cfg
import db
import apis
import telegram_utils as tg
import version

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("bot")


def validate_env() -> bool:
    ok = True
    if not cfg.TELEGRAM_TOKEN or not cfg.CHAT_ID:
        logger.warning("TELEGRAM_TOKEN / CHAT_ID missing – dry-run only")
    if not cfg.ETHERSCAN_API_KEY:
        logger.error("ETHERSCAN_API_KEY is required (get one free at https://etherscan.io/myapikey)")
        ok = False

    # Report plan tier and active chains
    plan_tier = getattr(cfg, "ETHERSCAN_PLAN_TIER", "free")
    active = sorted(cfg.active_chains()) if hasattr(cfg, "active_chains") else []
    paid_only = sorted(cfg.PAID_TIER_ONLY) if hasattr(cfg, "PAID_TIER_ONLY") else []

    logger.info("Etherscan plan tier: %s", plan_tier)
    logger.info("Active chains (%d): %s",
                len(active), ", ".join(active) or "NONE")
    if plan_tier in ("free", "") and paid_only:
        logger.info(
            "Paid-tier-only chains (filtered out on Free plan): %s",
            ", ".join(paid_only),
        )
        logger.info(
            "To enable BSC/Base/OP/Avalanche: upgrade your Etherscan plan at "
            "https://etherscan.io/myapikey (Lite+ = $199/mo, or use ETHERSCAN_PLAN_TIER=standard env var)"
        )

    # BSCSCAN_API_KEY is deprecated — V2 unified endpoint uses only ETHERSCAN_API_KEY
    if cfg.BSCSCAN_API_KEY:
        logger.warning(
            "BSCSCAN_API_KEY is set but DEPRECATED. Etherscan V2 uses a single "
            "ETHERSCAN_API_KEY for all chains. You can safely remove BSCSCAN_API_KEY."
        )

    return ok


def process_token(token: dict) -> tuple:
    contract = token.get("contract") or ""
    chain = (token.get("chain") or "").lower()
    if not contract or len(contract) < 10:
        return [], 0

    buyers = apis.find_early_buyers(contract, chain)
    if not buyers:
        logger.info("  no early buyers for %s", token.get("symbol"))
        return [], 0

    new_count = 0
    recorded = []
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
            recorded.append(b)
            logger.info("  trade recorded: %s… for %s", addr[:10], token.get("symbol"))

    # Only return newly recorded buyers — never fall back to all buyers (avoids TG spam on dups)
    return recorded, new_count


def main() -> int:
    print("=" * 60)
    print(f"🚀 {version.get_version_banner()} · Scan started {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 60)

    if not validate_env():
        return 1

    db.ensure_data_dir()
    start = time.time()

    gainers = apis.fetch_gainers()
    if not gainers:
        logger.info("No quality gainers found")
        tg.send_message(
            "🤖 *گزارش اجرای ربات*\n\n"
            "در این اجرا توکن باکیفیتی پیدا نشد\\.\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )
        return 0

    logger.info("Top gainers to inspect: %d", min(cfg.MAX_TOKENS_TO_CHECK, len(gainers)))

    valid = []
    new_trades = 0
    for token in gainers[: cfg.MAX_TOKENS_TO_CHECK]:
        symbol = token.get("symbol", "?")
        chain = token.get("chain", "?")
        logger.info("Checking %s on %s (Δ24h=%.1f%%)", symbol, chain, token.get("change_24h", 0))
        buyers, added = process_token(token)
        new_trades += added
        if added > 0 and buyers:
            # Only signal tokens where at least one NEW trade was saved
            valid.append((token, buyers))
            logger.info("  → %d NEW early buyers recorded", len(buyers))
        elif buyers is not None:
            logger.info("  → no new trades (all duplicates or filtered)")

    whitelist = db.get_whitelist_addresses()
    whales = db.get_whale_addresses()
    logger.info(
        "Summary: quality=%d | with_buyers=%d | new_trades=%d | whales=%d | wl=%d",
        len(gainers), len(valid), new_trades, len(whales), len(whitelist),
    )

    if cfg.SEND_DISCOVERY_SIGNALS and valid:
        report_n = min(cfg.REPORT_COUNT, len(valid))
        for token, buyers in valid[:report_n]:
            is_wl = any(b.get("address", "").lower() in whitelist for b in buyers)
            msg = tg.format_discovery_signal(token, buyers, is_wl)
            tg.send_message(msg)
            time.sleep(2.5)
    elif cfg.SEND_DISCOVERY_SIGNALS and not valid:
        logger.info("No new discovery signals to send (all duplicates)")

    tg.send_message(tg.format_bot_run_report({
        "total_tokens": len(gainers),
        "valid_tokens": len(valid),
        "new_wallets": new_trades,
        "whale_count": len(whales),
        "whitelist_count": len(whitelist),
    }))

    print(f"✅ Done in {time.time() - start:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
