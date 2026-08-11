# Crypto Wallet Bot

ربات تلگرامی برای پیدا کردن توکن‌های داغ از DexScreener، شناسایی **خریداران اولیه** از روی زنجیره (Etherscan V2)، ثبت معاملات، تشخیص فروش واقعی on-chain، امتیازدهی کیف‌پول‌ها و ساخت **Whitelist**.

## قابلیت‌ها (نسخه بهبودیافته)

- اسکن trending metas در DexScreener با فیلتر نقدینگی / حجم / رشد منطقی
- پیدا کردن خریداران اولیه واقعی (`to` در tokentx، بدون آدرس صفر و روترها)
- جلوگیری از ثبت تکراری trade برای یک کیف‌پول+توکن
- تشخیص فروش **on-chain** در جاب شبانه (نه فرض قیمت)
- امتیازدهی واقع‌گرایانه + whitelist با حداقل تعداد معامله
- پاکسازی کیف‌پول‌های قدیمی و دادهٔ خراب
- پشتیبانی چندزنجیره‌ای از طریق Etherscan API V2 (Ethereum, BSC, Base, Arbitrum, Polygon, …)
- نوشتن CSV اتمیک (بدون فایل نصفه)
- GitHub Actions زمان‌بندی‌شده

## ساختار

```
bot.py                 # اسکن اصلی + سیگنال
monitor_nightly.py     # فروش on-chain + امتیاز + گزارش شبانه
fix_data.py            # پاکسازی یک‌باره دادهٔ تاریخی
config.py              # تمام تنظیمات
db.py                  # لایه CSV
apis.py                # DexScreener + Etherscan
scoring.py             # امتیاز و whitelist
telegram_utils.py      # پیام‌ها
data/                  # wallets, trades, sells, whitelist
tests/                 # تست واحد
.github/workflows/     # bot / nightly / fix_data
```

## Secrets مورد نیاز (GitHub)

| Secret | توضیح |
|--------|--------|
| `TELEGRAM_TOKEN` | توکن ربات تلگرام |
| `CHAT_ID` | آیدی کانال/چت |
| `ETHERSCAN_API_KEY` | کلید Etherscan (برای اکثر زنجیره‌ها در V2) |
| `BSCSCAN_API_KEY` | اختیاری – برای BSC |

## اجرای محلی

```bash
pip install -r requirements.txt
export TELEGRAM_TOKEN=...
export CHAT_ID=...
export ETHERSCAN_API_KEY=...
export BSCSCAN_API_KEY=...   # optional

python bot.py
python monitor_nightly.py
python fix_data.py
```

## تست

```bash
python tests/test_logic.py
```

تست‌ها بدون نیاز به API key واقعی اجرا می‌شوند (mock).

## منطق مهم

### خریدار اولیه
- تراکنش‌های توکن از قدیمی به جدید
- آدرس `to` با مقدار معنادار
- حذف zero / dead / روترهای شناخته‌شده

### فروش
- فقط وقتی انتقال خروجی on-chain بعد از زمان خرید دیده شود
- سود/ضرر از قیمت فعلی DexScreener محاسبه و سقف‌گذاری می‌شود

### امتیاز
```
score = win_rate×0.45 + avg_profit×0.25 + timing×0.15 + activity×0.15
```
ورود به whitelist: `score ≥ 55` و حداقل ۳ معامله.

## زمان‌بندی Actions

- **bot**: هر ۴۵ دقیقه بین 02–20 UTC
- **nightly**: 22:30 UTC
- **fix_data**: فقط دستی

## نکات

- بدون `ETHERSCAN_API_KEY` ربات کار نمی‌کند.
- Rate-limit اتریوم‌اسکن را رعایت کنید (sleep داخلی وجود دارد).
- دادهٔ قدیمی پروژه قبلی ممکن است خراب باشد؛ یک‌بار `fix_data.py` را اجرا کنید.
