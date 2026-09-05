# Smart Whale Tracker

[![Version](https://img.shields.io/badge/version-2.7.1-blue)](./VERSION)
[![Python](https://img.shields.io/badge/python-3.11-green)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-brightgreen)](./LICENSE)

ربات کشف توکن داغ + شناسایی نهنگ از روی عملکرد واقعی on-chain + آلارم خرید/فروش نهنگ + داشبورد HTML.

**ریپو:** [Smart-Whale-Tracker](https://github.com) · **کانال:** Smart Whale Tracker · **نسخه:** [CHANGELOG](./CHANGELOG.md)

## 📌 نسخه‌بندی

این پروژه از [Semantic Versioning](https://semver.org/) استفاده می‌کند:

| نوع تغییر | مثال | چه زمانی |
|---|---|---|
| **PATCH** (1.2.3 → 1.2.4) | رفع باگ کوچک، tweak | تغییر رفتار نداریم، فقط bug fix |
| **MINOR** (1.2.3 → 1.3.0) | ویژگی جدید، بهبود رفتار | backwards compatible |
| **MAJOR** (1.2.3 → 2.0.0) | تغییر schema داده، حذف feature | breaking change |

ورژن فعلی در فایل `VERSION` ذخیره می‌شود و توسط `version.py` خوانده می‌شود.
همه لاگ‌های startup، پیام‌های تلگرام، و داشبورد ورژن را نمایش می‌دهند.

تاریخچه کامل تغییرات در [CHANGELOG.md](./CHANGELOG.md).

## 🆕 چه چیزی جدید در نسخه ۲

| ویژگی | تأثیر |
|---|---|
| **فرمول امتیازدهی نرمالایز** | +۱-۲ امتیاز به کیف‌پول‌های خوب → ۱ نهنگ فوری روی داده فعلی |
| **Thresholds بازشده** | wr 60→50, score 55→45 → نهنگ‌های بیشتری پیدا می‌شوند |
| **Backfill mode** | گذشته ۳۰ روزه کاندیدها → کشف فروش‌های پنهان (۱۵-۸ فروش بیشتر در ۲ هفته) |
| **Candidate alerts** | وقتی کیف‌پول اولین فروش سودده می‌کند، آلارم تلگرام 🥚 |
| **Weekly summary** | هر یکشنبه، ۵ کاندیدای برتر در گزارش شبانه |
| **API retry + backoff** | ۳ تلاش با exponential backoff → کاهش خطاها |
| **Price cache 60s** | کاهش ۳x فراخوانی API در طول nightly |
| **Multi-chain price** | DexScreener برای همه chains (بدون نیاز API key جدا) |
| **Historical price** | تخمین قیمت ۲۴ ساعت قبل برای محاسبه profit دقیق‌تر |
| **HTML dashboard** | `data/dashboard.html` بعد از هر nightly — داشبورد بصری کامل |
| **MarkdownV2 fix** | سه‌لایه fallback (V2 → legacy → plain) → هیچ پیامی گم نمی‌شود |
| **Concurrency locks** | GitHub Actions: جلوگیری از race condition روی data/ |
| **Whale dormancy** | نهنگ‌های غیرفعال (>۳۰ روز) به جای حذف، `dormant` می‌شوند |
| **Cap 80→500** | رفع باگ getattr — حالا ۱۰۰٪ tradeها اسکن می‌شوند |

## جریان کار

```
توکن داغ (bot.py)
    → خریداران اولیه + سیگنال «کشف اولیه»
         ↓
فروش on-chain با سود (nightly)
         ↓
FIRST winning sell → 🥚 candidate alert + backfill 30 days
         ↓
شرایط نهنگ برقرار شد → پیام «ارتقا به لیست نهنگ»
         ↓
مانیتور همان آدرس → آلارم «نهنگ خرید / نهنگ فروخت»
         ↓
داشبورد HTML به‌روزرسانی می‌شود
```

## قوانین نهنگ (`config.py`)

| شرط | مقدار پیش‌فرض |
|-----|----------------|
| فروش سودده تأییدشده | ≥ ۲ |
| WinRate | ≥ ۵۰٪ (نسخه ۲) |
| Score | ≥ ۴۵ (نسخه ۲) |
| تعداد معامله | ≥ ۳ |
| آخرین فعالیت | ≤ ۳۰ روز |

### کاندیدای نهنگ (سیستم جدید)
- حداقل ۱ فروش سودده تأییدشده
- میانگین سود ≥ ۵٪
- آلارم تلگرام جداگانه 🥚
- اولویت برای backfill

## Secrets (GitHub Actions)

- `TELEGRAM_TOKEN` (الزامی)
- `CHAT_ID` (الزامی)
- `ETHERSCAN_API_KEY` (الزامی — کلید واحد برای همه chains از طریق V2)
- `ETHERSCAN_PLAN_TIER` (اختیاری — `free` / `lite` / `standard` / `advanced` / `professional`)
  - پیش‌فرض: `free`
  - اگه پلن پولی داری، این رو تنظیم کن تا BSC/Base/OP/Avalanche فعال بشن

### Chains و Plan Tier

Etherscan V2 یک endpoint یکپارچه است، ولی در پلن رایگان فقط بعضی chains کار می‌کنند:

| Plan Tier | Chains فعال | Rate Limit |
|---|---|---|
| **Free** (پیش‌فرض) | Ethereum, Polygon, Arbitrum, Linea, Celo, Gnosis, Fantom, + 20 chains | 3 calls/sec, 100k/day |
| **Paid** (Lite+) | همه chains (شامل BSC, Base, OP, Avalanche) | 5-30 calls/sec, 100k-1.5M/day |

**Chains پولی (فقط در پلن paid):**
- BSC (`bsc`, `bnb`) — chainid 56
- Base (`base`) — chainid 8453
- Optimism (`optimism`) — chainid 10
- Avalanche (`avalanche`) — chainid 43114

اگه در پلن Free هستی، این chains به‌صورت خودکار فیلتر می‌شن (نه fail). برای فعال‌کردن:
1. در [etherscan.io/myapikey](https://etherscan.io/myapikey) پلن paid بخر
2. GitHub Secret جدید بساز: `ETHERSCAN_PLAN_TIER=standard` (یا tier خودت)

## ساختار

```
bot.py                 # کشف توکن + سیگنال اولیه (هر ۴۵ دقیقه)
monitor_nightly.py     # فروش + امتیاز + backfill + نهنگ + آلارم (روزانه)
dashboard.py           # HTML dashboard generator
fix_data.py            # پاکسازی داده (دستی)
config.py / db.py / apis.py / scoring.py / telegram_utils.py
version.py             # ورژن‌بندی متمرکز (VERSION file را می‌خواند)
VERSION                # ورژن فعلی (مثلاً 2.7.1)
CHANGELOG.md           # تاریخچه تغییرات
.gitignore             # data/* را ignore می‌کند (به‌جز .gitkeep)
data/.gitkeep          # placeholder برای دایرکتوری data/
data/                  # wallets, trades, sells, whitelist, whales, whale_alerts, dashboard.html
.github/workflows/     # bot | nightly | fix_data
tests/                 # ۲۳ تست واحد
```

## Actions

| Workflow | زمان | کار |
|----------|------|-----|
| Crypto Wallet Bot | هر ۴۵ دقیقه | کشف + سیگنال |
| Nightly Monitor & Cleanup | روزانه ۲۲:۳۰ UTC | فروش، نهنگ، آلارم، داشبورد |
| Fix Historical Data | دستی | پاکسازی |

## اجرای محلی

```bash
pip install -r requirements.txt
export TELEGRAM_TOKEN=... CHAT_ID=... ETHERSCAN_API_KEY=...

# 1. (یک‌بار) پاکسازی داده تاریخی
python fix_data.py

# 2. کشف توکن (هر ۴۵ دقیقه)
python bot.py

# 3. گزارش شبانه (روزانه)
python monitor_nightly.py

# 4. تولید داشبورد
python dashboard.py

# 5. ربات تعاملی (اختیاری، برای poll دستی)

# 6. تست‌ها
python tests/test_logic.py
```

## مشاهده داشبورد

بعد از هر nightly run، فایل `data/dashboard.html` تولید/به‌روزرسانی می‌شود.
این فایل را در مرورگر باز کنی — کاملاً self-contained (بدون نیاز به JS/CSS خارجی).

اگر روی GitHub Pages فعال کنی، می‌توانی به‌صورت زنده داشبورد را ببینی:
1. Settings → Pages → Source: `main` branch, `/data` folder
2. URL: `https://<username>.github.io/<repo>/dashboard.html`

## تنظیمات قابل تغییر (`config.py`)

### Thresholds (ساده‌ترین تنظیم)
```python
WHALE_MIN_WINNING_SELLS = 2      # کم کن برای نهنگ بیشتر
WHALE_MIN_WIN_RATE = 50.0        # کم کن برای نهنگ بیشتر (با نویز بیشتر)
WHALE_MIN_SCORE = 45.0           # کم کن برای نهنگ بیشتر
WHALE_MIN_TRADES = 3
WHALE_MAX_INACTIVE_DAYS = 30
```

### Backfill
```python
BACKFILL_ENABLED = True
BACKFILL_DAYS = 30               # عمق backfill
BACKFILL_MAX_WALLETS_PER_RUN = 5 # کم کن اگر به rate-limit خوردی
BACKFILL_MAX_TOKENS_PER_WALLET = 25
```

### Alerts
```python
ALERT_CANDIDATE_ENABLED = True   # آلارم کاندید
WEEKLY_SUMMARY_ENABLED = True
WEEKLY_SUMMARY_DAY = 6           # 0=Mon ... 6=Sun
```

## Migration از نسخه ۱

داده‌های فعلی شما به‌خوبی با نسخه ۲ کار می‌کنند — هیچ مهاجرت خاصی لازم نیست.

```bash
# فقط کد را جایگزین کن، سپس یک nightly اجرا کن
python monitor_nightly.py
# → ۱ نهنگ فوری promoted می‌شود (آدرس 0x750874e6fb8d)
# → ۸ کاندیدا آلارم می‌شوند
# → backfill شروع می‌شود و فروش‌های پنهان را پیدا می‌کند
```

## تست‌ها

```bash
python tests/test_logic.py
# → ۲۳ تست (۱۵ اصلی + ۸ جدید برای featureهای v2)
```

## محدودیت‌ها و نکات

- **Historical price**: تخمینی است — برای قبل از ۲۴ ساعت فقط current price استفاده می‌شود
- **Backfill rate-limit**: ۵ کیف‌پول در هر nightly — اگر خطای rate-limit دیدی، `BACKFILL_MAX_WALLETS_PER_RUN=3` کن
- **MarkdownV2**: اگر MarkdownV2 fail شود، خودکار به legacy Markdown و سپس plain text fallback می‌کند
- **Concurrency**: هر workflow گروه خودش را دارد → دو run هم‌زمان روی data/ رخ نمی‌دهد
- **Whale dormancy**: نهنگ‌های dormant در `whales.csv` باقی می‌مانند ولی مانیتور نمی‌شوند (برای تاریخچه)
