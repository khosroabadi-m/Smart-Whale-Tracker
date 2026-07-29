import json
import os
from datetime import datetime, timedelta
import sys
import requests

# ==================== گرفتن اطلاعات ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")

DB_PATH = "wallets_db.json"
MAX_AGE_DAYS = 30  # نگهداری کیف پول به مدت ۳۰ روز
MIN_SCORE_FOR_WHITELIST = 70  # حداقل امتیاز برای ورود به لیست سفید

# ==================== توابع دیتابیس ====================

def load_database():
    """بارگذاری دیتابیس از فایل JSON"""
    try:
        if os.path.exists(DB_PATH):
            with open(DB_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            return {"wallets": {}, "stats": {"total_wallets_tracked": 0, "whitelist_count": 0}}
    except Exception as e:
        print(f"❌ خطا در بارگذاری دیتابیس: {e}")
        return {"wallets": {}, "stats": {"total_wallets_tracked": 0, "whitelist_count": 0}}

def save_database(data):
    """ذخیره دیتابیس در فایل JSON"""
    try:
        with open(DB_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"❌ خطا در ذخیره دیتابیس: {e}")
        return False

# ==================== توابع ارسال پیام ====================

def send_telegram_message(message):
    """ارسال پیام به کانال تلگرام"""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("⚠️ اطلاعات تلگرام تنظیم نشده است.")
        return False
    
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
        print("✅ پیام شبانه با موفقیت ارسال شد.")
        return True
    except Exception as e:
        print(f"❌ خطا در ارسال پیام شبانه: {e}")
        return False

# ==================== توابع اصلی ====================

def check_token_performance(contract_address, chain_name="ethereum"):
    """بررسی عملکرد یک توکن برای محاسبه سود"""
    if not ETHERSCAN_API_KEY:
        return None
    
    chain_map = {
        "ethereum": 1, "eth": 1, "bsc": 56, "bnb": 56,
        "arbitrum": 42161, "optimism": 10, "polygon": 137,
        "base": 8453, "linea": 59144
    }
    
    chain_id_num = chain_map.get(chain_name.lower(), 1)
    url = f"https://api.etherscan.io/v2/api?chainid={chain_id_num}&module=account&action=tokentx&contractaddress={contract_address}&sort=asc&apikey={ETHERSCAN_API_KEY}"
    
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get("status") == "1":
            return len(data.get("result", []))
    except:
        pass
    return None

def calculate_wallet_score(wallet_data):
    """محاسبه امتیاز کیف پول بر اساس عملکرد"""
    total_trades = wallet_data.get("total_trades", 0)
    if total_trades == 0:
        return 0
    
    # محاسبه نرخ برد (با فرض اینکه ۵۰٪ معاملات موفق باشند)
    winning_trades = wallet_data.get("winning_trades", 0)
    win_rate = (winning_trades / total_trades) * 100
    
    # محاسبه میانگین سود (با فرض اینکه ۱۰٪ متوسط سود باشد)
    avg_profit = wallet_data.get("average_profit_percent", 10)
    
    # امتیاز ترکیبی
    score = (win_rate * 0.6) + (avg_profit * 0.4)
    
    # پاداش برای تعداد معاملات بیشتر
    if total_trades > 5:
        score += 5
    if total_trades > 10:
        score += 10
    if total_trades > 20:
        score += 15
    
    return round(score, 2)

def get_current_price(symbol):
    """دریافت قیمت فعلی ارز (ساده شده)"""
    # این تابع در نسخه کامل باید قیمت واقعی را از API بگیرد
    # فعلاً یک قیمت فرضی برمی‌گرداند
    return 0.001

def run_nightly_monitor():
    """اجرای فرآیند شبانه - امتیازدهی و پاکسازی"""
    print("="*60)
    print(f"🌙 شروع فرآیند شبانه در {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    db = load_database()
    wallets = db.get("wallets", {})
    
    if not wallets:
        print("ℹ️ هیچ کیف پولی در دیتابیس وجود ندارد.")
        return
    
    print(f"📊 تعداد کل کیف پول‌ها: {len(wallets)}")
    
    # ۱. به‌روزرسانی امتیازها
    print("\n🔄 به‌روزرسانی امتیاز کیف پول‌ها...")
    updated_count = 0
    whitelist_count = 0
    
    for address, data in wallets.items():
        # محاسبه امتیاز
        score = calculate_wallet_score(data)
        data["score"] = score
        
        # بررسی ورود به لیست سفید
        if score >= MIN_SCORE_FOR_WHITELIST:
            if not data.get("in_whitelist", False):
                data["in_whitelist"] = True
                print(f"⭐ کیف پول جدید در لیست سفید: {address[:10]}... (امتیاز: {score})")
            whitelist_count += 1
        else:
            data["in_whitelist"] = False
        
        updated_count += 1
    
    db["stats"]["whitelist_count"] = whitelist_count
    print(f"✅ امتیاز {updated_count} کیف پول به‌روز شد.")
    print(f"⭐ تعداد کیف پول‌های سفید: {whitelist_count}")
    
    # ۲. حذف کیف پول‌های قدیمی (بیش از ۳۰ روز)
    print("\n🗑️ حذف کیف پول‌های غیرفعال (بیش از ۳۰ روز)...")
    now = datetime.utcnow()
    removed_count = 0
    old_wallets = []
    
    for address, data in wallets.items():
        last_seen = data.get("last_seen", "")
        if last_seen:
            try:
                last_date = datetime.fromisoformat(last_seen)
                days_diff = (now - last_date).days
                if days_diff > MAX_AGE_DAYS:
                    old_wallets.append(address)
                    removed_count += 1
            except:
                continue
    
    for address in old_wallets:
        del wallets[address]
        print(f"🗑️ حذف کیف پول: {address[:10]}... (غیرفعال بیش از {MAX_AGE_DAYS} روز)")
    
    if removed_count > 0:
        print(f"✅ تعداد کیف پول‌های حذف شده: {removed_count}")
        db["stats"]["total_wallets_tracked"] = len(wallets)
    else:
        print("ℹ️ هیچ کیف پول قدیمی برای حذف وجود ندارد.")
    
    # ۳. ذخیره دیتابیس
    print("\n💾 ذخیره دیتابیس...")
    if save_database(db):
        print("✅ دیتابیس با موفقیت ذخیره شد.")
    else:
        print("❌ خطا در ذخیره دیتابیس.")
        return
    
    # ۴. ارسال گزارش به تلگرام
    print("\n📤 ارسال گزارش شبانه...")
    message = f"""
🌙 **گزارش شبانه**

📊 **آمار دیتابیس:**
▫️ تعداد کل کیف پول‌ها: {len(wallets)}
▫️ تعداد کیف پول‌های سفید: {whitelist_count}
▫️ کیف پول‌های حذف شده: {removed_count}

🏆 **۵ کیف پول برتر:**
"""
    # مرتب‌سازی بر اساس امتیاز و نمایش ۵ تا برتر
    sorted_wallets = sorted(wallets.items(), key=lambda x: x[1].get("score", 0), reverse=True)
    for i, (address, data) in enumerate(sorted_wallets[:5], 1):
        score = data.get("score", 0)
        trades = data.get("total_trades", 0)
        chain = data.get("chain", "نامشخص")
        short_addr = address[:8] + "..." + address[-6:]
        message += f"{i}. `{short_addr}` ({chain}) - امتیاز: {score} - تعداد معاملات: {trades}\n"
    
    if TELEGRAM_TOKEN and CHAT_ID:
        send_telegram_message(message)
    
    print("\n" + "="*60)
    print(f"✅ فرآیند شبانه در {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} به پایان رسید.")
    print("="*60)

if __name__ == "__main__":
    run_nightly_monitor()
