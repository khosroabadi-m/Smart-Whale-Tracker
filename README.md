# Smart Whale Tracker

ربات کشف توکن داغ + شناسایی نهنگ از روی عملکرد واقعی on-chain + آلارم خرید/فروش نهنگ.

**ریپو:** [Smart-Whale-Tracker](https://github.com) · **کانال:** Smart Whale Tracker

## جریان کار

```
توکن داغ (bot)
    → خریداران اولیه + سیگنال «کشف اولیه»
         ↓
فروش on-chain با سود (nightly)
         ↓
شرایط نهنگ برقرار شد → پیام «ارتقا به لیست نهنگ»
         ↓
مانیتور همان آدرس → آلارم «نهنگ خرید / نهنگ فروخت»
```

## قوانین نهنگ (`config.py`)

| شرط | مقدار پیش‌فرض |
|-----|----------------|
| فروش سودده تأییدشده | ≥ ۲ |
| WinRate | ≥ ۶۰٪ |
| Score | ≥ ۵۵ |
| تعداد معامله | ≥ ۳ |
| آخرین فعالیت | ≤ ۳۰ روز |

## Secrets (GitHub Actions)

- `TELEGRAM_TOKEN`
- `CHAT_ID`
- `ETHERSCAN_API_KEY`
- `BSCSCAN_API_KEY` (اختیاری)

## ساختار

```
bot.py                 # کشف توکن + سیگنال اولیه
monitor_nightly.py     # فروش + امتیاز + نهنگ + آلارم
fix_data.py            # پاکسازی داده
config.py / db.py / apis.py / scoring.py / telegram_utils.py
data/                  # wallets, trades, sells, whitelist, whales, whale_alerts
.github/workflows/     # bot | nightly | fix_data
tests/
```

## Actions

| Workflow | زمان | کار |
|----------|------|-----|
| Smart Whale – Discovery | هر ۴۵ دقیقه | کشف + سیگنال |
| Smart Whale – Nightly | روزانه | فروش، نهنگ، آلارم |
| Smart Whale – Fix Data | دستی | پاکسازی |

## اجرای محلی

```bash
pip install -r requirements.txt
export TELEGRAM_TOKEN=... CHAT_ID=... ETHERSCAN_API_KEY=...
python fix_data.py
python bot.py
python monitor_nightly.py
python tests/test_logic.py
```
