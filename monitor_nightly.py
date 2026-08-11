import csv
import os
from datetime import datetime, timedelta
import requests
import sys

# ==================== تنظیمات ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

DATA_DIR = "data"
WALLETS_FILE = os.path.join(DATA_DIR, "wallets.csv")
TRADES_FILE = os.path.join(DATA_DIR, "trades.csv")
SELLS_FILE = os.path.join(DATA_DIR, "sells.csv")
WHITELIST_FILE = os.path.join(DATA_DIR, "whitelist.csv")

MAX_AGE_DAYS = 30
MIN_SCORE_FOR_WHITELIST = 70
MAX_REASONABLE_PROFIT = 50  # ✅ کاهش یافته از ۲۰۰ به ۵۰

# ==================== توابع CSV ====================

def read_csv(file_path, headers):
    if not os.path.exists(file_path):
        return []
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        return list(reader)

def write_csv(file_path, headers, data):
    with open(file_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(data)

def get_wallet_headers():
    return ["address", "chain", "first_seen", "last_seen", "total_trades",
            "total_sells", "winning_sells", "losing_sells", "win_rate",
            "avg_profit", "avg_hold_duration", "score", "in_whitelist"]

def get_whitelist_headers():
    return ["rank", "wallet_address", "chain", "score",
            "total_trades", "win_rate", "avg_profit", "last_seen"]

# ==================== محاسبه امتیاز (اصلاح‌شده) ====================

def calculate_score(wallet):
    """
    محاسبه امتیاز کیف پول با وزن‌های جدید:
    - نرخ برد: ۵۵٪ (افزایش یافته)
    - میانگین سود: ۲۰٪ (کاهش یافته)
    """
    total_trades = int(wallet.get("total_trades", 0))
    total_sells = int(wallet.get("total_sells", 0))
    winning_sells = int(wallet.get("winning_sells", 0))
    losing_sells = int(wallet.get("losing_sells", 0))
    
    # اگر فروشی انجام نشده، امتیاز صفر است
    if total_sells == 0:
        return 0.0
    
    # ۱. نرخ برد (وزن ۵۵٪)
    win_rate = (winning_sells / total_sells) * 100
    
    # ۲. میانگین سود (وزن ۲۰٪)
    sells = read_csv(SELLS_FILE, ["sell_id", "trade_id", "wallet_address", "token",
                                   "sell_price", "sell_date", "sell_percent",
                                   "profit_percent", "is_winning", "hold_duration_hours"])
    
    wallet_sells = [s for s in sells if s.get("wallet_address") == wallet.get("address")]
    total_profit = sum(float(s.get("profit_percent", 0)) for s in wallet_sells)
    avg_profit = total_profit / total_sells if total_sells > 0 else 0
    
    # ✅ محدود کردن avg_profit به بازه منطقی (۰ تا ۵۰)
    if avg_profit > MAX_REASONABLE_PROFIT:
        print(f"⚠️ avg_profit غیرطبیعی ({avg_profit:.2f}) برای {wallet.get('address', '')[:10]}... محدود به {MAX_REASONABLE_PROFIT} شد.")
        avg_profit = MAX_REASONABLE_PROFIT
    
    # ۳. مدت زمان نگهداری (وزن ۱۵٪)
    total_duration = sum(float(s.get("hold_duration_hours", 0)) for s in wallet_sells)
    avg_duration = total_duration / total_sells if total_sells > 0 else 0
    timing_score = (avg_profit / (avg_duration + 1)) * 10 if avg_duration >= 0 else 0
    
    # ۴. مدیریت سرمایه (وزن ۱۰٪)
    partial_sells = sum(1 for s in wallet_sells if float(s.get("sell_percent", 0)) < 100)
    capital_management_score = (partial_sells / total_trades) * 10 if total_trades > 0 else 0
    
    # ✅ امتیاز نهایی با وزن‌های جدید
    score = (win_rate * 0.55) + (avg_profit * 0.20) + (timing_score * 0.15) + (capital_management_score * 0.10)
    
    return round(score, 2)

# ==================== به‌روزرسانی امتیازها ====================

def update_all_scores():
    """به‌روزرسانی امتیاز همه کیف پول‌ها"""
    print("🔄 به‌روزرسانی امتیاز کیف پول‌ها...")
    
    wallets = read_csv(WALLETS_FILE, get_wallet_headers())
    updated_count = 0
    
    for wallet in wallets:
        score = calculate_score(wallet)
        wallet["score"] = str(score)
        
        # بررسی ورود به لیست سفید
        if score >= MIN_SCORE_FOR_WHITELIST and int(wallet.get("total_trades", 0)) >= 5:
            wallet["in_whitelist"] = "TRUE"
        else:
            wallet["in_whitelist"] = "FALSE"
        
        updated_count += 1
    
    write_csv(WALLETS_FILE, get_wallet_headers(), wallets)
    print(f"✅ امتیاز {updated_count} کیف پول به‌روز شد.")

# ==================== پاکسازی کیف پول‌های قدیمی ====================

def cleanup_old_wallets():
    """حذف کیف پول‌هایی که بیش از ۳۰ روز فعال نبوده‌اند"""
    print("\n🗑️ پاکسازی کیف پول‌های قدیمی...")
    
    wallets = read_csv(WALLETS_FILE, get_wallet_headers())
    now = datetime.utcnow()
    removed_count = 0
    
    active_wallets = []
    for wallet in wallets:
        last_seen = wallet.get("last_seen", "")
        if last_seen:
            try:
                last_date = datetime.fromisoformat(last_seen)
                if (now - last_date).days <= MAX_AGE_DAYS:
                    active_wallets.append(wallet)
                else:
                    removed_count += 1
                    print(f"🗑️ حذف کیف پول: {wallet.get('address', '')[:10]}...")
            except:
                active_wallets.append(wallet)
        else:
            active_wallets.append(wallet)
    
    write_csv(WALLETS_FILE, get_wallet_headers(), active_wallets)
    print(f"✅ {removed_count} کیف پول قدیمی حذف شد.")
    return len(active_wallets)

# ==================== به‌روزرسانی لیست سفید ====================

def update_whitelist():
    """به‌روزرسانی لیست سفید بر اساس کیف پول‌های با امتیاز بالا"""
    print("\n⭐ به‌روزرسانی لیست سفید...")
    
    wallets = read_csv(WALLETS_FILE, get_wallet_headers())
    
    # فیلتر کیف پول‌های سفید
    whitelist_wallets = [w for w in wallets if w.get("in_whitelist") == "TRUE"]
    
    # مرتب‌سازی بر اساس امتیاز
    whitelist_wallets.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
    
    # ذخیره لیست سفید
    whitelist_data = []
    for rank, wallet in enumerate(whitelist_wallets, 1):
        whitelist_data.append({
            "rank": str(rank),
            "wallet_address": wallet.get("address", ""),
            "chain": wallet.get("chain", ""),
            "score": wallet.get("score", "0"),
            "total_trades": wallet.get("total_trades", "0"),
            "win_rate": wallet.get("win_rate", "0"),
            "avg_profit": wallet.get("avg_profit", "0"),
            "last_seen": wallet.get("last_seen", "")
        })
    
    write_csv(WHITELIST_FILE, get_whitelist_headers(), whitelist_data)
    print(f"✅ لیست سفید با {len(whitelist_data)} کیف پول به‌روز شد.")

# ==================== ارسال گزارش شبانه ====================

def send_nightly_report():
    """ارسال گزارش شبانه به تلگرام"""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠️ اطلاعات تلگرام تنظیم نشده است.")
        return
    
    wallets = read_csv(WALLETS_FILE, get_wallet_headers())
    whitelist = read_csv(WHITELIST_FILE, get_whitelist_headers())
    
    # محاسبه آمار
    total_wallets = len(wallets)
    total_whitelist = len(whitelist)
    total_trades = sum(int(w.get("total_trades", 0)) for w in wallets)
    total_sells = sum(int(w.get("total_sells", 0)) for w in wallets)
    
    # ۵ کیف پول برتر
    sorted_wallets = sorted(wallets, key=lambda x: float(x.get("score", 0)), reverse=True)
    top_wallets = sorted_wallets[:5]
    
    message = f"""
🌙 **گزارش شبانه**

📊 **آمار کلی:**
▫️ تعداد کل کیف پول‌ها: {total_wallets}
▫️ تعداد کیف پول‌های سفید: {total_whitelist}
▫️ مجموع معاملات: {total_trades}
▫️ مجموع فروش‌ها: {total_sells}

🏆 **۵ کیف پول برتر:**
"""
    
    for i, wallet in enumerate(top_wallets, 1):
        score = wallet.get("score", "0")
        address = wallet.get("address", "")[:12] + "..."
        trades = wallet.get("total_trades", "0")
        message += f"{i}. `{address}` - امتیاز: {score} - معاملات: {trades}\n"
    
    # ارسال به تلگرام
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        print("✅ گزارش شبانه با موفقیت ارسال شد.")
    except Exception as e:
        print(f"❌ خطا در ارسال گزارش شبانه: {e}")

# ==================== تابع اصلی ====================

def main():
    """اجرای فرآیند شبانه"""
    print("="*60)
    print(f"🌙 شروع فرآیند شبانه در {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    # ۱. به‌روزرسانی امتیازها
    update_all_scores()
    
    # ۲. پاکسازی کیف پول‌های قدیمی
    cleanup_old_wallets()
    
    # ۳. به‌روزرسانی لیست سفید
    update_whitelist()
    
    # ۴. ارسال گزارش شبانه
    send_nightly_report()
    
    print("\n" + "="*60)
    print(f"✅ فرآیند شبانه در {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} به پایان رسید.")
    print("="*60)

if __name__ == "__main__":
    main()
