"""Telegram message helpers – discovery, whale, reports."""
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional

import requests

import config as cfg
import db

logger = logging.getLogger(__name__)


def send_message(text: str) -> bool:
    if not cfg.TELEGRAM_TOKEN or not cfg.CHAT_ID:
        logger.warning("Telegram credentials missing – dry-run")
        print("--- TELEGRAM (dry-run) ---")
        print(text[:2000])
        print("--- END ---")
        return False

    url = f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}/sendMessage"
    if len(text) > 4000:
        text = text[:3900] + "\n\n… (خلاصه شد)"
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


def _short(addr: str) -> str:
    if not addr or len(addr) < 14:
        return addr or "?"
    return f"{addr[:8]}…{addr[-6:]}"


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M UTC")


def format_discovery_signal(token: Dict, buyers: List[Dict], is_whitelisted: bool) -> str:
    whitelist = db.get_whitelist_addresses()
    whales = db.get_whale_addresses()
    price = float(token.get("price") or 0)
    change = float(token.get("change_24h") or 0)
    volume = float(token.get("volume") or 0)
    liquidity = float(token.get("liquidity") or 0)
    whale_buyers = [b for b in buyers if (b.get("address") or "").lower() in whales]
    tag = "🐋 نهنگ در خریداران" if whale_buyers else "🔍 کشف اولیه"

    lines = [
        tag,
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "📊 *توکن داغ*",
        f"▫️ نام: {token.get('name', '?')} (${token.get('symbol', '?')})",
        f"▫️ شبکه: `{token.get('chain', '?')}`",
        f"▫️ دسته: {token.get('meta_name', '?')}",
        f"▫️ قیمت: `${price:.8g}`",
        f"▫️ رشد ۲۴س: *{change:.1f}%*",
        f"▫️ حجم: `${volume:,.0f}` | نقدینگی: `${liquidity:,.0f}`",
        "",
        f"🐋 *خریداران اولیه ({len(buyers)})*",
    ]
    for i, b in enumerate(buyers[:6], 1):
        addr = b.get("address", "")
        amount = float(b.get("amount") or 0)
        marks = []
        if addr.lower() in whales:
            marks.append("🐋")
        if addr.lower() in whitelist:
            marks.append("⭐")
        mark = (" " + " ".join(marks)) if marks else ""
        lines.append(f"{i}\\. `{_short(addr)}` — {amount:,.0f} توکن{mark}")

    lines += [
        "",
        "📝 *توضیح:* این سیگنال «کشف» است؛ هنوز لزوماً نهنگ تأییدشده نیست\\.",
        "اگر همین آدرس‌ها چند بار با سود بفروشند → به لیست نهنگ می‌روند و",
        "از آن به بعد هر خرید/فروش‌شان جداگانه آلارم می‌شود\\.",
        "",
        f"🔗 [DexScreener]({token.get('dex_url', '#')})",
        f"📄 [Contract](https://etherscan.io/token/{token.get('contract', '')})",
        f"⏰ {_now()}",
    ]
    return "\n".join(lines)


def format_bot_run_report(summary: Dict) -> str:
    return "\n".join([
        "🤖 *گزارش اجرای ربات (کشف)*",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "این جاب توکن‌های داغ را از DexScreener می‌گیرد،",
        "خریداران اولیه on-chain را پیدا می‌کند و در دیتابیس ثبت می‌کند\\.",
        "",
        f"▫️ توکن‌های باکیفیت: *{summary.get('total_tokens', 0)}*",
        f"▫️ با خریدار اولیه: *{summary.get('valid_tokens', 0)}*",
        f"▫️ trade جدید ثبت‌شده: *{summary.get('new_wallets', 0)}*",
        f"▫️ تعداد نهنگ‌های فعال: *{summary.get('whale_count', 0)}*",
        f"▫️ whitelist: *{summary.get('whitelist_count', 0)}*",
        "",
        "مرحله بعد: جاب شبانه فروش‌ها را چک می‌کند و",
        "در صورت عملکرد خوب، آدرس را به *لیست نهنگ* ارتقا می‌دهد\\.",
        f"⏰ {_now()}",
    ])


def format_whale_promoted(wallet: Dict) -> str:
    addr = wallet.get("address", "")
    return "\n".join([
        "🐋✨ *ارتقا به لیست نهنگ*",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"آدرس: `{addr}`",
        f"شبکه: `{wallet.get('chain', 'ethereum')}`",
        "",
        "📈 *عملکرد ثبت‌شده*",
        f"▫️ امتیاز: *{wallet.get('score', '0')}*",
        f"▫️ نرخ برد: *{wallet.get('win_rate', '0')}%*",
        f"▫️ فروش‌های سودده: *{wallet.get('winning_sells', '0')}*",
        f"▫️ کل معاملات: *{wallet.get('total_trades', '0')}*",
        f"▫️ میانگین سود: *{wallet.get('avg_profit', '0')}%*",
        "",
        "📝 *یعنی چه؟*",
        "این کیف‌پول چند بار با سود واقعی on-chain فروخته است\\.",
        "از این لحظه در *واچ‌لیست نهنگ* است\\.",
        "هر خرید یا فروش بعدی‌اش جداگانه آلارم می‌شود",
        "تا بتوانی همراهش وارد یا خارج شوی\\.",
        "",
        f"🔗 [Etherscan](https://etherscan.io/address/{addr})",
        f"⏰ {_now()}",
    ])


def format_whale_buy(whale: Dict, event: Dict, price: float = 0.0) -> str:
    addr = whale.get("address") or ""
    token = event.get("token_symbol", "?")
    contract = event.get("contract", "")
    amount = float(event.get("amount") or 0)
    chain = event.get("chain") or whale.get("chain") or "ethereum"
    tx = event.get("hash", "")

    lines = [
        "🐋🟢 *نهنگ خرید کرد*",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"آدرس: `{_short(addr)}`",
        f"امتیاز: {whale.get('score', '?')} | WinRate: {whale.get('win_rate', '?')}% | سودده: {whale.get('winning_sells', '?')}",
        "",
        f"▫️ توکن: *${token}* ({event.get('token_name', '')})",
        f"▫️ شبکه: `{chain}`",
        f"▫️ مقدار تقریبی: `{amount:,.2f}`",
    ]
    if price > 0:
        lines.append(f"▫️ قیمت تقریبی: `${price:.8g}`")
    lines += [
        "",
        "📝 *اقدام پیشنهادی:* بررسی توکن و در صورت تأیید، ورود هم‌جهت با نهنگ\\.",
        "این آلارم فقط برای آدرس‌های داخل لیست نهنگ ارسال می‌شود\\.",
        "",
    ]
    if contract:
        lines.append(f"🔗 [DexScreener](https://dexscreener.com/{chain}/{contract})")
        lines.append(f"📄 [Token](https://etherscan.io/token/{contract})")
    if tx:
        lines.append(f"🧾 [Tx](https://etherscan.io/tx/{tx})")
    lines.append(f"⏰ {_now()}")
    return "\n".join(lines)


def format_whale_sell(whale: Dict, event: Dict, price: float = 0.0, profit_pct: Optional[float] = None) -> str:
    addr = whale.get("address") or ""
    token = event.get("token_symbol", "?")
    contract = event.get("contract", "")
    amount = float(event.get("amount") or 0)
    chain = event.get("chain") or whale.get("chain") or "ethereum"
    tx = event.get("hash", "")

    lines = [
        "🐋🔴 *نهنگ فروخت*",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"آدرس: `{_short(addr)}`",
        f"امتیاز: {whale.get('score', '?')} | WinRate: {whale.get('win_rate', '?')}%",
        "",
        f"▫️ توکن: *${token}*",
        f"▫️ شبکه: `{chain}`",
        f"▫️ مقدار تقریبی خروجی: `{amount:,.2f}`",
    ]
    if price > 0:
        lines.append(f"▫️ قیمت تقریبی: `${price:.8g}`")
    if profit_pct is not None:
        sign = "+" if profit_pct >= 0 else ""
        lines.append(f"▫️ سود/ضرر تخمینی: *{sign}{profit_pct:.1f}%*")
    lines += [
        "",
        "📝 *اقدام پیشنهادی:* بررسی خروج از پوزیشن هم‌جهت با نهنگ\\.",
        "",
    ]
    if contract:
        lines.append(f"🔗 [DexScreener](https://dexscreener.com/{chain}/{contract})")
    if tx:
        lines.append(f"🧾 [Tx](https://etherscan.io/tx/{tx})")
    lines.append(f"⏰ {_now()}")
    return "\n".join(lines)


def format_nightly_report(stats: Dict, top: List[Dict], new_whales: int, whale_events: int) -> str:
    lines = [
        "🌙 *گزارش شبانه*",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "📝 *کار این جاب:*",
        "۱\\) فروش on-chain روی tradeهای باز",
        "۲\\) به‌روز کردن امتیاز کیف‌پول‌ها",
        "۳\\) ارتقا به لیست نهنگ در صورت واجد شرایط بودن",
        "۴\\) مانیتور خرید/فروش نهنگ‌های فعال",
        "",
        "📊 *آمار*",
        f"▫️ کل کیف‌پول‌ها: {stats.get('total_wallets', 0)}",
        f"▫️ فروش جدید ثبت‌شده: {stats.get('new_sells', 0)}",
        f"▫️ نهنگ‌های فعال: {stats.get('total_whales', 0)}",
        f"▫️ نهنگ جدید این اجرا: {new_whales}",
        f"▫️ حرکت نهنگ آلارم‌شده: {whale_events}",
        f"▫️ whitelist: {stats.get('total_whitelist', 0)}",
        "",
        "🏆 *۵ کیف‌پول برتر*",
    ]
    for i, w in enumerate(top[:5], 1):
        addr = w.get("address", "")
        whale_mark = " 🐋" if (w.get("is_whale") or "").upper() == "TRUE" else ""
        lines.append(
            f"{i}\\. `{_short(addr)}` — {w.get('score', '0')} "
            f"(W{w.get('winning_sells', '0')}/{w.get('total_sells', '0')}){whale_mark}"
        )
    lines += [
        "",
        "اگر هنوز نهنگ نداری یعنی فروش سوددهٔ تأییدشدهٔ کافی جمع نشده\\.",
        "با ادامهٔ bot + nightly این لیست پر می‌شود\\.",
        f"⏰ {_now()}",
    ]
    return "\n".join(lines)
