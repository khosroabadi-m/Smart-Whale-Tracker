"""Telegram message helpers."""
import logging
from datetime import datetime, timezone
from typing import List, Dict

import requests

import config as cfg
import db

logger = logging.getLogger(__name__)


def send_message(text: str) -> bool:
    if not cfg.TELEGRAM_TOKEN or not cfg.CHAT_ID:
        logger.warning("Telegram credentials missing – message not sent")
        print("--- TELEGRAM MESSAGE (dry-run) ---")
        print(text[:1500])
        print("--- END ---")
        return False

    url = f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": cfg.CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=12)
        r.raise_for_status()
        logger.info("Telegram message sent")
        return True
    except Exception as e:
        logger.error("Telegram send failed: %s", e)
        return False


def format_signal(token: Dict, buyers: List[Dict], is_whitelisted: bool) -> str:
    whitelist = db.get_whitelist_addresses()
    price = float(token.get("price") or 0)
    change = float(token.get("change_24h") or 0)
    volume = float(token.get("volume") or 0)
    liquidity = float(token.get("liquidity") or 0)
    mcap = float(token.get("market_cap") or 0)

    lines = [
        "🦄 *سیگنال معاملاتی – خریداران اولیه*",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "📊 *اطلاعات ارز*",
        f"▫️ نام: {token.get('name', '?')} (${token.get('symbol', '?')})",
        f"▫️ شبکه: `{token.get('chain', '?')}`",
        f"▫️ دسته: {token.get('meta_name', '?')}",
        f"▫️ صرافی: {token.get('dex', '?')}",
        f"▫️ قیمت: `${price:,.8f}`".rstrip("0").rstrip("."),
        f"▫️ رشد ۲۴س: *{change:.1f}%*",
        f"▫️ حجم: `${volume:,.0f}`",
        f"▫️ نقدینگی: `${liquidity:,.0f}`",
        f"▫️ مارکت‌کپ: `${mcap:,.0f}`",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"🐋 *خریداران اولیه ({len(buyers)})*",
    ]

    for i, b in enumerate(buyers[:6], 1):
        addr = b.get("address", "")
        short = f"{addr[:8]}…{addr[-6:]}" if len(addr) > 16 else addr
        amount = float(b.get("amount") or 0)
        star = " ⭐ WL" if addr.lower() in whitelist else ""
        lines.append(f"{i}\\. `{short}` — {amount:,.0f} توکن{star}")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "📈 *تحلیل*",
        f"▫️ کیف‌پول سفید در لیست: {'✅ بله' if is_whitelisted else '❌ خیر'}",
        f"▫️ نقدینگی: {'✅ خوب' if liquidity >= 50000 else '⚠️ متوسط'}",
        "",
        f"🔗 [DexScreener]({token.get('dex_url', '#')})",
        f"📊 [Contract](https://etherscan.io/token/{token.get('contract', '')})",
        "",
        f"⏰ {datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M UTC')}",
    ]
    return "\n".join(lines)


def format_performance(summary: Dict) -> str:
    return (
        "📊 *گزارش عملکرد ربات*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"▫️ ارزهای باکیفیت: {summary.get('total_tokens', 0)}\n"
        f"▫️ با خریدار اولیه: {summary.get('valid_tokens', 0)}\n"
        f"▫️ کیف‌پول جدید/به‌روز: {summary.get('new_wallets', 0)}\n"
        f"▫️ تعداد whitelist: {summary.get('whitelist_count', 0)}\n\n"
        f"⏰ {datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M UTC')}"
    )


def format_nightly(stats: Dict, top: List[Dict]) -> str:
    lines = [
        "🌙 *گزارش شبانه*",
        "",
        "📊 *آمار کلی*",
        f"▫️ کل کیف‌پول‌ها: {stats.get('total_wallets', 0)}",
        f"▫️ whitelist: {stats.get('total_whitelist', 0)}",
        f"▫️ مجموع معاملات: {stats.get('total_trades', 0)}",
        f"▫️ مجموع فروش‌ها: {stats.get('total_sells', 0)}",
        "",
        "🏆 *۵ کیف‌پول برتر*",
    ]
    for i, w in enumerate(top[:5], 1):
        addr = w.get("address", "")
        short = f"{addr[:10]}…" if len(addr) > 12 else addr
        lines.append(
            f"{i}\\. `{short}` — امتیاز {w.get('score', '0')} "
            f"(معاملات: {w.get('total_trades', '0')})"
        )
    lines.append(f"\n⏰ {datetime.now(timezone.utc).replace(tzinfo=None).strftime('%Y-%m-%d %H:%M UTC')}")
    return "\n".join(lines)
