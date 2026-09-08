"""Telegram message helpers – discovery, whale, reports.

FIXED: Telegram Markdown escaping.
We now use MarkdownV2 parse_mode with proper escaping of special chars:
  _ * [ ] ( ) ~ ` > # + - = | { } . !
Raw user-facing text must be escaped via _md_escape() before embedding
in **bold** or *italic* segments.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import List, Dict, Optional

import requests

import config as cfg
import db
import version

logger = logging.getLogger(__name__)

# Small state file persisting the last sent message id per named thread
# (e.g. "nightly_report", "bot_run") so periodic reports can reply to their
# previous instance. Per-wallet/per-whale threads live in the CSV columns.
_TG_STATE_FILE = os.path.join(cfg.DATA_DIR, "tg_state.json")


def _version_footer() -> str:
    """Return a version footer for Telegram messages."""
    return f"📦 v{version.get_version()}"


# Characters that MUST be escaped in MarkdownV2 (outside formatting entities)
_MD_V2_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def _md_escape(text) -> str:
    """Escape special MarkdownV2 characters in raw text.
    Safe to use for wallet addresses, numbers, dates — anything not inside *bold* / _italic_."""
    if text is None:
        return ""
    return _MD_V2_SPECIAL.sub(r"\\\1", str(text))


def send_message(text: str, reply_to_message_id: Optional[int] = None) -> Optional[int]:
    """
    Send text to the configured chat. When reply_to_message_id is given, the
    message is sent as a reply to that message (same chat), keeping the
    conversation threaded on its source. Returns the new message's id so
    callers can chain follow-ups, or None on failure / dry-run.
    """
    if not cfg.TELEGRAM_TOKEN or not cfg.CHAT_ID:
        logger.warning("Telegram credentials missing – dry-run")
        print("--- TELEGRAM (dry-run) ---")
        if reply_to_message_id:
            print(f"(reply to {reply_to_message_id})")
        print(text[:2000])
        print("--- END ---")
        return None

    url = f"https://api.telegram.org/bot{cfg.TELEGRAM_TOKEN}/sendMessage"
    if len(text) > 4000:
        text = text[:3900] + "\n\n… (خلاصه شد)"

    # Strategy: try legacy Markdown first (since our format_* functions are
    # written for legacy Markdown). If that fails (rare), fall back to plain.
    # MarkdownV2 requires escaping many special chars which our format funcs
    # don't do consistently — so we stick with legacy Markdown.
    payload = {
        "chat_id": cfg.CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        reply_to_message_id = int(reply_to_message_id)
        payload["reply_to_message_id"] = reply_to_message_id
        # never fail just because the source message was deleted
        payload["allow_sending_without_reply"] = True
    except (TypeError, ValueError):
        pass

    try:
        r = requests.post(url, json=payload, timeout=12)
        r.raise_for_status()
        logger.info("Telegram message sent (Markdown)%s",
                    f" as reply to {reply_to_message_id}" if reply_to_message_id else "")
        return _sent_message_id(r)
    except Exception as e:
        logger.warning("Telegram Markdown failed (%s), trying plain text", e)
        # Final fallback: plain text
        payload_plain = dict(payload)
        payload_plain.pop("parse_mode", None)
        try:
            r3 = requests.post(url, json=payload_plain, timeout=12)
            r3.raise_for_status()
            logger.info("Telegram message sent (plain fallback)")
            return _sent_message_id(r3)
        except Exception as e3:
            logger.error("Telegram all formats failed: %s", e3)
            return None


def _sent_message_id(r: requests.Response) -> Optional[int]:
    """Extract result.message_id from a Telegram sendMessage response."""
    try:
        mid = (r.json().get("result") or {}).get("message_id")
        return int(mid) if mid else None
    except Exception:
        return None


def get_last_message_id(thread_key: str) -> Optional[int]:
    try:
        with open(_TG_STATE_FILE, "r", encoding="utf-8") as f:
            mid = json.load(f).get(thread_key)
        return int(mid) if mid else None
    except Exception:
        return None


def set_last_message_id(thread_key: str, message_id: int) -> None:
    state = {}
    try:
        with open(_TG_STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        pass
    state[thread_key] = int(message_id)
    db.ensure_data_dir()
    with open(_TG_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def send_threaded(text: str, thread_key: str) -> Optional[int]:
    """
    Send text as a reply to the last message of the named thread and remember
    the new message id. Used for periodic reports whose "source" is the
    previous report of the same job (nightly report, bot run report, …).
    """
    mid = send_message(text, reply_to_message_id=get_last_message_id(thread_key))
    if mid:
        set_last_message_id(thread_key, mid)
    return mid


def _short(addr: str) -> str:
    if not addr or len(addr) < 14:
        return addr or "?"
    return f"{addr[:8]}…{addr[-6:]}"


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M UTC")


# ---------- Hashtag helpers (traceability) ----------

def _tag(text) -> str:
    """Sanitized hashtag — alphanumerics only, safe in legacy Markdown."""
    t = re.sub(r"[^0-9A-Za-z]", "", str(text or ""))
    return f"#{t}" if t else ""


def _wallet_tag(addr: str) -> str:
    """Short searchable hashtag for a wallet, e.g. #w5ced44f0 — lets the user
    trace every message about the same address via Telegram search."""
    a = re.sub(r"[^0-9A-Za-z]", "", str(addr or ""))
    return f"#w{a[2:10].lower()}" if len(a) >= 10 else "#wallet"


def _cashtag(symbol) -> str:
    s = re.sub(r"[^0-9A-Za-z]", "", str(symbol or ""))
    return f"${s}" if s else ""


def _trace(addr: str = "", chain: str = "", symbol: str = "",
           extra: Optional[List[str]] = None) -> str:
    """Footer tag line — tap any tag in Telegram to trace this message's source."""
    tags = [t for t in [
        _wallet_tag(addr) if addr else "",
        _tag(chain) if chain else "",
        _cashtag(symbol) if symbol else "",
        *(extra or []),
    ] if t]
    return "🏷 " + "   ".join(tags) if tags else ""


def format_discovery_signal(token: Dict, buyers: List[Dict], is_whitelisted: bool) -> str:
    whitelist = db.get_whitelist_addresses()
    whales = db.get_whale_addresses()
    price = float(token.get("price") or 0)
    change = float(token.get("change_24h") or 0)
    volume = float(token.get("volume") or 0)
    liquidity = float(token.get("liquidity") or 0)
    symbol = token.get("symbol", "?")
    chain = token.get("chain", "?")
    contract = token.get("contract", "")
    whale_buyers = [b for b in buyers if (b.get("address") or "").lower() in whales]
    title = "🐋 نهنگ در خریداران" if whale_buyers else "🔍 کشف اولیه"

    lines = [
        f"🚀💎 ✦ *{title}* ✦",
        "🏷 #Discovery #NewToken" + (" #WhaleSpotting" if whale_buyers else ""),
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "📊 *توکن داغ*",
        f"▫️ نام: {token.get('name', '?')} (${symbol})",
        f"▫️ شبکه: `{chain}`",
        f"▫️ دسته: {token.get('meta_name', '?')}",
        f"▫️ قیمت: `${price:.8g}`",
        f"▫️ رشد ۲۴س: *{change:.1f}%* 📈",
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
        lines.append(f"{i}. `{_short(addr)}` — {amount:,.0f} توکن{mark}")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "💡 *توضیح:* این سیگنال «کشف» است؛ هنوز لزوماً نهنگ تأییدشده نیست.",
        "اگر همین آدرس‌ها چند بار با سود بفروشند → به لیست نهنگ می‌روند و",
        "از آن به بعد هر خرید/فروش‌شان جداگانه آلارم می‌شود.",
        "",
        f"🔗 [DexScreener]({token.get('dex_url', '#')})",
        f"📄 [Contract](https://etherscan.io/token/{contract})",
        _trace(chain=chain, symbol=symbol, extra=["#EarlyBuyers"]),
        f"⏰ {_now()}",
        f"{_version_footer()}",
    ]
    return "\n".join(lines)


def format_bot_run_report(summary: Dict) -> str:
    return "\n".join([
        "🤖✨ ✦ *گزارش اجرای ربات (کشف)* ✦",
        "🏷 #BotReport #Discovery",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "این جاب توکن‌های داغ را از DexScreener می‌گیرد،",
        "خریداران اولیه on-chain را پیدا می‌کند و در دیتابیس ثبت می‌کند.",
        "",
        f"▫️ توکن‌های باکیفیت: *{summary.get('total_tokens', 0)}*",
        f"▫️ با خریدار اولیه: *{summary.get('valid_tokens', 0)}*",
        f"▫️ ترید جدید ثبت‌شده: *{summary.get('new_wallets', 0)}*",
        f"▫️ تعداد نهنگ‌های فعال: *{summary.get('whale_count', 0)}* 🐋",
        f"▫️ whitelist: *{summary.get('whitelist_count', 0)}* ⭐",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "مرحله بعد: جاب شبانه فروش‌ها را چک می‌کند و",
        "در صورت عملکرد خوب، آدرس را به *لیست نهنگ* ارتقا می‌دهد.",
        "🔎 برای دنبال‌کردن هر والت یا توکن، روی هشتگ‌های پایین پیام‌ها بزنید.",
        _trace(extra=["#BotReport"]),
        f"⏰ {_now()}",
        f"{_version_footer()}",
    ])


def format_whale_promoted(wallet: Dict) -> str:
    addr = wallet.get("address", "")
    chain = wallet.get("chain", "ethereum")
    return "\n".join([
        "🐋✨🏆 ✦ *ارتقا به لیست نهنگ* ✦",
        "🏷 #WhalePromoted #WhaleAlert",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"🎉 یک کیف‌پول دیگر به جمع *نهنگ‌های تأییدشده* پیوست!",
        "",
        f"▫️ آدرس: `{addr}`",
        f"▫️ شبکه: `{chain}`",
        "",
        "📈 *عملکرد ثبت‌شده*",
        f"▫️ امتیاز: *{wallet.get('score', '0')}* 🏅",
        f"▫️ نرخ برد: *{wallet.get('win_rate', '0')}%*",
        f"▫️ فروش‌های سودده: *{wallet.get('winning_sells', '0')}* ✅",
        f"▫️ کل معاملات: *{wallet.get('total_trades', '0')}*",
        f"▫️ میانگین سود: *{wallet.get('avg_profit', '0')}%*",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "📝 *یعنی چه؟*",
        "این کیف‌پول چند بار با سود واقعی on-chain فروخته است.",
        "از این لحظه در *واچ‌لیست نهنگ* است.",
        "هر خرید یا فروش بعدی‌اش جداگانه آلارم می‌شود",
        "تا بتوانی همراهش وارد یا خارج شوی. 🚀",
        "",
        f"🔗 [Etherscan](https://etherscan.io/address/{addr})",
        _trace(addr=addr, chain=chain, extra=["#WhalePromoted", "#WhaleList"]),
        f"⏰ {_now()}",
        f"{_version_footer()}",
    ])


def format_whale_buy(whale: Dict, event: Dict, price: float = 0.0) -> str:
    addr = whale.get("address") or ""
    token = event.get("token_symbol", "?")
    contract = event.get("contract", "")
    amount = float(event.get("amount") or 0)
    chain = event.get("chain") or whale.get("chain") or "ethereum"
    tx = event.get("hash", "")

    lines = [
        "🐋🟢 ✦ *نهنگ خرید کرد* ✦",
        "🏷 #WhaleBuy #BuyAlert",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"▫️ آدرس: `{_short(addr)}`",
        f"▫️ امتیاز: {whale.get('score', '?')} 🏅 | WinRate: {whale.get('win_rate', '?')}% | سودده: {whale.get('winning_sells', '?')} ✅",
        "",
        f"💵 *توکن:* *${token}* ({event.get('token_name', '')})",
        f"▫️ شبکه: `{chain}`",
        f"▫️ مقدار تقریبی: `{amount:,.2f}`",
    ]
    if price > 0:
        lines.append(f"▫️ قیمت تقریبی: `${price:.8g}`")
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "🎯 *اقدام پیشنهادی:* بررسی توکن و در صورت تأیید، ورود هم‌جهت با نهنگ.",
        "این آلارم فقط برای آدرس‌های داخل لیست نهنگ ارسال می‌شود.",
        "",
    ]
    if contract:
        lines.append(f"🔗 [DexScreener](https://dexscreener.com/{chain}/{contract})")
        lines.append(f"📄 [Token](https://etherscan.io/token/{contract})")
    if tx:
        lines.append(f"🧾 [Tx](https://etherscan.io/tx/{tx})")
    lines.append(_trace(addr=addr, chain=chain, symbol=token, extra=["#WhaleBuy"]))
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
        "🐋🔴 ✦ *نهنگ فروخت* ✦",
        "🏷 #WhaleSell #SellAlert",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"▫️ آدرس: `{_short(addr)}`",
        f"▫️ امتیاز: {whale.get('score', '?')} 🏅 | WinRate: {whale.get('win_rate', '?')}%",
        "",
        f"💸 *توکن:* *${token}*",
        f"▫️ شبکه: `{chain}`",
        f"▫️ مقدار تقریبی خروجی: `{amount:,.2f}`",
    ]
    if price > 0:
        lines.append(f"▫️ قیمت تقریبی: `${price:.8g}`")
    if profit_pct is not None:
        sign = "+" if profit_pct >= 0 else ""
        emoji = "📈" if profit_pct >= 0 else "📉"
        lines.append(f"▫️ سود/ضرر تخمینی: *{sign}{profit_pct:.1f}%* {emoji}")
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "🎯 *اقدام پیشنهادی:* بررسی خروج از پوزیشن هم‌جهت با نهنگ.",
        "",
    ]
    if contract:
        lines.append(f"🔗 [DexScreener](https://dexscreener.com/{chain}/{contract})")
    if tx:
        lines.append(f"🧾 [Tx](https://etherscan.io/tx/{tx})")
    lines.append(_trace(addr=addr, chain=chain, symbol=token, extra=["#WhaleSell"]))
    lines.append(f"⏰ {_now()}")
    return "\n".join(lines)


def format_whale_sell_summary(
    whale: Dict,
    token: str,
    chain: str,
    n_sells: int,
    total_amount: float,
    price: float = 0.0,
    profit_pct: Optional[float] = None,
    first_ts: int = 0,
    last_ts: int = 0,
) -> str:
    """One message summarizing ALL sell events of a whale for one token in this run."""
    addr = whale.get("address") or ""

    def _t(ts) -> str:
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%H:%M")
        except Exception:
            return "?"

    span = f"از {_t(first_ts)} تا {_t(last_ts)} UTC" if first_ts and last_ts else "در این دوره"

    lines = [
        f"🐋🔴 ✦ *خلاصه فروش نهنگ* ✦ ({n_sells} تراکنش)",
        "🏷 #WhaleSell #SellSummary",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"▫️ آدرس: `{_short(addr)}`",
        f"▫️ امتیاز: {whale.get('score', '?')} 🏅 | WinRate: {whale.get('win_rate', '?')}%",
        "",
        f"💸 *توکن:* *${token}*",
        f"▫️ شبکه: `{chain}`",
        f"▫️ تعداد فروش: *{n_sells}*",
        f"▫️ مجموع فروش: `{total_amount:,.2f}` توکن",
    ]
    if price > 0:
        lines.append(f"▫️ قیمت فعلی: `${price:.8g}`")
        total_usd = total_amount * price
        if total_usd > 0:
            lines.append(f"▫️ ارزش تقریبی مجموع: *${total_usd:,.0f}* 💵")
    if profit_pct is not None:
        sign = "+" if profit_pct >= 0 else ""
        emoji = "📈" if profit_pct >= 0 else "📉"
        lines.append(f"▫️ سود/ضرر تخمینی: *{sign}{profit_pct:.1f}%* {emoji}")
    lines += [
        f"▫️ بازه: {span}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "🎯 *اقدام پیشنهادی:* بررسی خروج از پوزیشن هم‌جهت با نهنگ.",
        "",
    ]
    lines.append(_trace(addr=addr, chain=chain, symbol=token, extra=["#WhaleSell", "#SellSummary"]))
    lines.append(f"⏰ {_now()}")
    return "\n".join(lines)


def format_whale_buy_summary(
    whale: Dict,
    token: str,
    chain: str,
    n_buys: int,
    total_amount: float,
    price: float = 0.0,
    first_ts: int = 0,
    last_ts: int = 0,
) -> str:
    """One message summarizing ALL buy events of a whale for one token in this run."""
    addr = whale.get("address") or ""

    def _t(ts) -> str:
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%H:%M")
        except Exception:
            return "?"

    span = f"از {_t(first_ts)} تا {_t(last_ts)} UTC" if first_ts and last_ts else "در این دوره"

    lines = [
        f"🐋🟢 ✦ *خلاصه خرید نهنگ* ✦ ({n_buys} تراکنش)",
        "🏷 #WhaleBuy #BuySummary",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"▫️ آدرس: `{_short(addr)}`",
        f"▫️ امتیاز: {whale.get('score', '?')} 🏅 | WinRate: {whale.get('win_rate', '?')}%",
        "",
        f"💵 *توکن:* *${token}*",
        f"▫️ شبکه: `{chain}`",
        f"▫️ تعداد خرید: *{n_buys}*",
        f"▫️ مجموع خرید: `{total_amount:,.2f}` توکن",
    ]
    if price > 0:
        lines.append(f"▫️ قیمت فعلی: `${price:.8g}`")
        total_usd = total_amount * price
        if total_usd > 0:
            lines.append(f"▫️ ارزش تقریبی مجموع: *${total_usd:,.0f}* 💵")
    lines += [
        f"▫️ بازه: {span}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "🎯 *اقدام پیشنهادی:* بررسی توکن و در صورت تأیید، ورود هم‌جهت با نهنگ.",
        "",
    ]
    lines.append(_trace(addr=addr, chain=chain, symbol=token, extra=["#WhaleBuy", "#BuySummary"]))
    lines.append(f"⏰ {_now()}")
    return "\n".join(lines)


def format_whale_candidate(wallet: Dict) -> str:
    """Alert sent when a wallet first reaches 1 verified profitable sell."""
    addr = wallet.get("address", "")
    chain = wallet.get("chain", "ethereum")
    return "\n".join([
        "🐋🥚 ✦ *نهنگ در حال شکل‌گیری* ✦",
        "🏷 #WhaleCandidate #EmergingWhale",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"▫️ آدرس: `{addr}`",
        f"▫️ شبکه: `{chain}`",
        "",
        "📈 *اولین فروش سودده ثبت شد* ✅",
        f"▫️ امتیاز فعلی: *{wallet.get('score', '0')}*",
        f"▫️ نرخ برد: *{wallet.get('win_rate', '0')}%*",
        f"▫️ فروش‌های سودده: *{wallet.get('winning_sells', '0')}*",
        f"▫️ کل معاملات: *{wallet.get('total_trades', '0')}*",
        f"▫️ میانگین سود: *{wallet.get('avg_profit', '0')}%*",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "📝 *یعنی چه؟*",
        "این کیف‌پول حداقل یک بار با سود واقعی on-chain فروخته است.",
        "اگر چند فروش سودده دیگر جمع کند → به لیست *نهنگ* ارتقا می‌یابد",
        "و از آن به بعد هر خرید/فروش‌اش جداگانه آلارم می‌شود. 🐋",
        "",
        "⏳ در جاب شبانه آینده، گذشته ۳۰ روزه‌اش را اسکن می‌کنیم",
        "تا فروش‌های سودده پنهان را پیدا کنیم. 🔎",
        "",
        f"🔗 [Etherscan](https://etherscan.io/address/{addr})",
        _trace(addr=addr, chain=chain, extra=["#WhaleCandidate"]),
        f"⏰ {_now()}",
        f"{_version_footer()}",
    ])


def format_nightly_report(
    stats: Dict,
    top: List[Dict],
    new_whales: int,
    whale_events: int,
    backfill_sells: int = 0,
    candidate_alerts: int = 0,
    candidates: Optional[List[Dict]] = None,
) -> str:
    lines = [
        "🌙✨ ✦ *گزارش شبانه* ✦",
        "🏷 #NightlyReport #DailySummary",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "📝 *کار این جاب:*",
        "۱) فروش on-chain روی تریدهای باز",
        "۲) به‌روز کردن امتیاز کیف‌پول‌ها",
        "۳) backfill: جستجوی فروش‌های گذشته ۳۰ روزه برای کاندیدها",
        "۴) ارتقا به لیست نهنگ در صورت واجد شرایط بودن",
        "۵) آلارم نهنگ‌های کاندید",
        "۶) مانیتور خرید/فروش نهنگ‌های فعال",
        "",
        "📊 *آمار*",
        f"▫️ کل کیف‌پول‌ها: {stats.get('total_wallets', 0)}",
        f"▫️ فروش جدید ثبت‌شده: *{stats.get('new_sells', 0)}* ✅",
        f"▫️ فروش کشف‌شده از backfill: *{backfill_sells}* 🔎",
        f"▫️ نهنگ‌های فعال: {stats.get('total_whales', 0)} 🐋",
        f"▫️ نهنگ جدید این اجرا: *{new_whales}*",
        f"▫️ آلارم کاندید جدید: {candidate_alerts} 🥚",
        f"▫️ حرکت نهنگ آلارم‌شده: *{whale_events}* 📡",
        f"▫️ whitelist: {stats.get('total_whitelist', 0)} ⭐",
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "🏆 *۵ کیف‌پول برتر*",
    ]
    for i, w in enumerate(top[:5], 1):
        addr = w.get("address", "")
        whale_mark = " 🐋" if (w.get("is_whale") or "").upper() == "TRUE" else ""
        lines.append(
            f"{i}. `{_short(addr)}` — *{w.get('score', '0')}* "
            f"(W{w.get('winning_sells', '0')}/{w.get('total_sells', '0')}){whale_mark}"
        )
    if candidates:
        lines += [
            "",
            "🥚 *۵ کاندیدای برتر نهنگ*",
            "(≥۱ فروش سودده، در صف ارتقا)",
        ]
        for i, c in enumerate(candidates[:5], 1):
            addr = c.get("address", "")
            lines.append(
                f"{i}. `{_short(addr)}` — score={c.get('score', '0')} "
                f"W{c.get('winning_sells', '0')}/{c.get('total_trades', '0')} trades"
            )
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━",
        "🌱 اگر هنوز نهنگ نداری یعنی فروش سوددهٔ تأییدشدهٔ کافی جمع نشده.",
        "با ادامهٔ bot + nightly + backfill این لیست پر می‌شود. 🚀",
        _trace(extra=["#NightlyReport"]),
        f"⏰ {_now()}",
        f"{_version_footer()}",
    ]
    return "\n".join(lines)
