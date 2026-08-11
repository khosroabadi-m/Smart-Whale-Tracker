# Crypto Wallet Bot + Whale Tracker

ربات کشف توکن داغ + شناسایی نهنگ از روی عملکرد واقعی + آلارم خرید/فروش نهنگ.

## جریان

1. **bot.py** — توکن داغ از DexScreener → خریداران اولیه → سیگنال «کشف»
2. **monitor_nightly.py**
   - فروش on-chain روی tradeهای باز
   - امتیازدهی
   - اگر چند فروش سودده داشت → **ارتقا به نهنگ**
   - مانیتور نهنگ‌ها: هر خرید/فروش جدید → آلارم تلگرام

## قوانین نهنگ (config.py)

- حداقل ۲ فروش سودده تأییدشده
- WinRate ≥ ۶۰٪
- Score ≥ ۵۵
- حداقل ۳ معامله
- فعال در ۳۰ روز اخیر

## Secrets

- `TELEGRAM_TOKEN`
- `CHAT_ID`
- `ETHERSCAN_API_KEY`
- `BSCSCAN_API_KEY` (اختیاری)

## فایل‌های data

| فایل | نقش |
|------|-----|
| wallets.csv | همه کیف‌پول‌ها + امتیاز |
| trades.csv | خریدهای ثبت‌شده |
| sells.csv | فروش‌های on-chain |
| whitelist.csv | امتیاز بالا |
| whales.csv | **نهنگ‌های فعال** |
| whale_alerts.csv | تاریخچه آلارم‌ها |

## Actions

- `Crypto Wallet Bot` — هر ۴۵ دقیقه (کشف)
- `Nightly Monitor & Cleanup` — روزانه (فروش + نهنگ + آلارم)
- `Fix Historical Data` — دستی

## تست

```bash
python tests/test_logic.py
python fix_data.py
python bot.py
python monitor_nightly.py
```
