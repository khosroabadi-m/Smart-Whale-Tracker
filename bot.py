import requests
import os
import json
import time
from datetime import datetime, timedelta
import sys

# ==================== گرفتن اطلاعات ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")

if not TELEGRAM_TOKEN or not CHAT_ID or not ETHERSCAN_API_KEY:
    print("❌ خطا: توکن، آیدی کانال یا کلید اتریوم در Secrets تنظیم نشده است.")
    sys.exit(1)

# ==================== آدرس‌های API ====================
TRENDING_METAS_URL = "https://api.dexscreener.com/metas/trending/v1"
META_DETAILS_URL = "https://api.dexscreener.com/metas/meta/v1"

# ==================== تنظیمات ====================
CONFIG = {
    "MIN_LIQUIDITY_DEX": 30000,
    "MIN_VOLUME_DEX": 5000,
    "MIN_CHANGE_24H": 10,
    "MAX_CHANGE_24H": 500,
    "REPORT_COUNT": 5,
    "SUPPORTED_CHAINS": [
        "ethereum", "eth", "bsc", "bnb", "arbitrum", 
        "optimism", "polygon", "base", "linea", 
        "avalanche", "fantom"
    ]
}

DB_PATH = "wallets_db.json"

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

def update_wallet(wallet_address, token_info, chain, price):
    """اضافه کردن یا به‌روزرسانی یک کیف پول در دیتابیس"""
    db = load_database()
    now = datetime.utcnow().isoformat()
    
    if wallet_address not in db["wallets"]:
        db["wallets"][wallet_address] = {
            "address": wallet_address,
            "chain": chain,
            "first_seen": now,
            "last_seen": now,
            "trades": [],
            "total_trades": 0,
            "winning_trades": 0,
            "total_profit_percent": 0,
            "average_profit_percent": 0,
            "score": 0,
            "in_whitelist": False
        }
        db["stats"]["total_wallets_tracked"] += 1
    
    db["wallets"][wallet_address]["last_seen"] = now
    db["wallets"][wallet_address]["trades"].append({
        "token": token_info.get("symbol", "نامشخص"),
        "token_name": token_info.get("name", "نامشخص"),
        "buy_price": price,
        "buy_date": now,
        "status": "open"
    })
    db["wallets"][wallet_address]["total_trades"] += 1
    
    save_database(db)
    return db

def get_whitelist():
    """دریافت لیست کیف پول‌های سفید"""
    db = load_database()
    whitelist = []
    for address, data in db["wallets"].items():
        if data.get("in_whitelist", False):
            whitelist.append(address)
    return whitelist

# ==================== توابع ارسال پیام ====================

def send_telegram_message(message):
    """ارسال پیام به کانال تلگرام"""
    print("📤 در حال ارسال پیام به تلگرام...")
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
        print("✅ پیام با موفقیت به کانال ارسال شد.")
        return True
    except Exception as e:
        print(f"❌ خطا در ارسال پیام: {e}")
        return False

# ==================== توابع دریافت از DexScreener ====================

def get_trending_metas():
    """دریافت لیست دسته‌بندی‌های داغ از DexScreener"""
    print("🔍 [DEX-۱] دریافت لیست دسته‌بندی‌های داغ از DexScreener...")
    try:
        response = requests.get(TRENDING_METAS_URL, timeout=10)
        response.raise_for_status()
        data = response.json()
        print(f"✅ [DEX-۲] تعداد دسته‌بندی‌های دریافت شده: {len(data)}")
        return data
    except Exception as e:
        print(f"❌ [DEX] خطا در دریافت دسته‌بندی‌ها: {e}")
        return []

def get_tokens_from_meta(slug):
    """دریافت لیست توکن‌های یک دسته‌بندی خاص از DexScreener"""
    try:
        url = f"{META_DETAILS_URL}/{slug}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        pairs = data.get("pairs", [])
        print(f"   📊 [DEX] تعداد توکن‌های دسته {slug}: {len(pairs)}")
        return pairs
    except Exception as e:
        print(f"   ⚠️ [DEX] خطا در دریافت توکن‌های دسته {slug}: {e}")
        return []

def is_valid_dex_token(token_info):
    """بررسی کیفیت توکن DEX با فیلترهای مختلف"""
    chain = token_info.get("chain", "").lower()
    
    if chain not in CONFIG["SUPPORTED_CHAINS"]:
        return False
    
    liquidity = token_info.get("liquidity", 0)
    if liquidity < CONFIG["MIN_LIQUIDITY_DEX"]:
        print(f"   ⏭️ [DEX] نقدینگی پایین: ${liquidity:,.0f}")
        return False
    
    volume = token_info.get("volume", 0)
    if volume < CONFIG["MIN_VOLUME_DEX"]:
        print(f"   ⏭️ [DEX] حجم پایین: ${volume:,.0f}")
        return False
    
    change = token_info.get("change_24h", 0)
    if change < CONFIG["MIN_CHANGE_24H"]:
        print(f"   ⏭️ [DEX] رشد پایین: {change:.2f}%")
        return False
    
    if change > CONFIG["MAX_CHANGE_24H"]:
        print(f"   ⏭️ [DEX] رشد غیرعادی: {change:.2f}%")
        return False
    
    return True

def get_gainers_from_dex():
    """پیدا کردن ارزهای با رشد بالا از DexScreener"""
    print("="*60)
    print("🚀 [DEX] شروع جستجو در صرافی‌های غیرمتمرکز")
    print("="*60)
    
    metas = get_trending_metas()
    if not metas:
        print("❌ [DEX] هیچ دسته‌بندی داغی پیدا نشد.")
        return []
    
    top_metas = []
    for meta in metas:
        change_24h = meta.get("marketCapChange", {}).get("h24", 0)
        if change_24h >= 3:
            top_metas.append({
                "slug": meta.get("slug"),
                "name": meta.get("name", "نامشخص"),
                "change_24h": change_24h
            })
    
    print(f"📊 [DEX] تعداد دسته‌بندی‌های با رشد +۳٪: {len(top_metas)}")
    
    if not top_metas:
        print("ℹ️ [DEX] هیچ دسته‌بندی با رشد بالا پیدا نشد.")
        return []
    
    print("\n🏆 [DEX] دسته‌بندی‌های برتر:")
    for i, meta in enumerate(top_metas[:5], 1):
        print(f"   {i}. {meta['name']} (رشد دسته: {meta['change_24h']:.2f}%)")
    
    all_gainers = []
    seen_tokens = set()
    filtered_count = 0
    
    for meta in top_metas[:5]:
        slug = meta["slug"]
        print(f"\n🔎 [DEX] بررسی دسته: {meta['name']} ({slug})")
        
        pairs = get_tokens_from_meta(slug)
        token_count = 0
        
        for pair in pairs:
            try:
                base_token = pair.get("baseToken", {})
                token_symbol = base_token.get("symbol", "نامشخص")
                token_address = base_token.get("address", "")
                
                token_key = f"{pair.get('chainId', '')}-{token_address}"
                if token_key in seen_tokens:
                    continue
                seen_tokens.add(token_key)
                
                price_change = pair.get("priceChange", {})
                change_24h = price_change.get("h24", 0)
                
                token_info = {
                    "name": base_token.get("name", "نامشخص"),
                    "symbol": token_symbol,
                    "chain": pair.get("chainId", "ناشناخته"),
                    "price": pair.get("priceUsd", "0"),
                    "change_24h": change_24h,
                    "volume": pair.get("volume", {}).get("h24", 0),
                    "liquidity": pair.get("liquidity", {}).get("usd", 0),
                    "dex_url": pair.get("url", "#"),
                    "contract": token_address,
                    "dex": pair.get("dexId", "نامشخص"),
                    "market_cap": pair.get("marketCap", 0),
                    "meta_name": meta["name"],
                    "source": "DEX"
                }
                
                if is_valid_dex_token(token_info):
                    all_gainers.append(token_info)
                    token_count += 1
                    print(f"   ✅ [DEX] توکن باکیفیت: {token_symbol} (رشد: {change_24h:.2f}%)")
                else:
                    filtered_count += 1
                    
            except Exception as e:
                print(f"   ⚠️ [DEX] خطا در پردازش: {e}")
                continue
        
        print(f"   📊 [DEX] تعداد توکن‌های باکیفیت در این دسته: {token_count}")
    
    print(f"\n📈 [DEX] تعداد کل ارزهای باکیفیت پیدا شده: {len(all_gainers)}")
    print(f"⏭️ [DEX] تعداد ارزهای فیلتر شده: {filtered_count}")
    
    return all_gainers

# ==================== توابع پیدا کردن خریداران اولیه ====================

def get_first_buyers_evm(contract_address, chain_name="ethereum"):
    """پیدا کردن خریداران اولیه با Etherscan API V2"""
    chain_map = {
        "ethereum": 1, "eth": 1, "bsc": 56, "bnb": 56,
        "arbitrum": 42161, "optimism": 10, "polygon": 137,
        "base": 8453, "linea": 59144, "avalanche": 43114,
        "fantom": 250
    }
    
    chain_id_num = chain_map.get(chain_name.lower(), 1)
    
    url = f"https://api.etherscan.io/v2/api?chainid={chain_id_num}&module=account&action=tokentx&contractaddress={contract_address}&sort=asc&apikey={ETHERSCAN_API_KEY}"
    
    print(f"🔗 [EVM] در حال بررسی قرارداد: {contract_address[:10]}...{contract_address[-6:]} در شبکه {chain_name}")
    
    try:
        response = requests.get(url, timeout=15)
        data = response.json()
        
        if data.get("status") != "1":
            print(f"⚠️ [EVM] خطای Etherscan: {data.get('message', 'خطای ناشناخته')}")
            return []
        
        transactions = data.get("result", [])
        print(f"📊 [EVM] تعداد کل تراکنش‌های قرارداد: {len(transactions)}")
        
        buyers = {}
        for tx in transactions:
            try:
                from_addr = tx.get("from")
                decimals = int(tx.get("tokenDecimal", 18))
                value = float(tx.get("value", 0)) / (10 ** decimals)
                if value > 0 and from_addr not in buyers:
                    buyers[from_addr] = {
                        "amount": value,
                        "timestamp": tx.get("timeStamp"),
                        "hash": tx.get("hash")
                    }
                    if len(buyers) >= 5:
                        break
            except (ValueError, TypeError):
                continue
        
        result = [{"address": addr, **data} for addr, data in buyers.items()]
        result.sort(key=lambda x: x["timestamp"])
        print(f"✅ [EVM] تعداد خریداران اولیه پیدا شده: {len(result)}")
        return result
        
    except Exception as e:
        print(f"❌ [EVM] خطا در Etherscan: {e}")
        return []

def get_first_buyers(contract_address, chain_name):
    """تشخیص شبکه و دریافت خریداران اولیه"""
    if not contract_address or len(contract_address) < 10:
        print(f"⚠️ آدرس قرارداد نامعتبر")
        return []
    
    chain = chain_name.lower()
    
    if chain in ["ethereum", "eth", "bsc", "bnb", "arbitrum", "optimism", "polygon", "base", "linea", "avalanche", "fantom"]:
        return get_first_buyers_evm(contract_address, chain)
    else:
        print(f"ℹ️ شبکه {chain} پشتیبانی نمی‌شود.")
        return []

# ==================== ساخت پیام گزارش ====================

def format_message(token, buyers=None):
    """ساخت پیام گزارش برای یک توکن"""
    # گرفتن لیست سفید برای نمایش
    whitelist = get_whitelist()
    is_whitelisted = False
    if buyers:
        for buyer in buyers:
            if buyer["address"] in whitelist:
                is_whitelisted = True
                break
    
    message = f"""
🦄 **ارز با خریدار اولیه شناسایی شد!**
▫️ نام: {token.get('name', 'نامشخص')} (${token.get('symbol', 'نامشخص')})
▫️ شبکه: {token.get('chain', 'نامشخص')}
▫️ دسته‌بندی: {token.get('meta_name', 'نامشخص')}
▫️ صرافی: {token.get('dex', 'نامشخص')}
▫️ قیمت: ${float(token.get('price', 0)):,.4f}
▫️ رشد ۲۴ ساعته: **{token.get('change_24h', 0):.2f}%** ✅
▫️ حجم معاملات: ${token.get('volume', 0):,.0f}
▫️ نقدینگی: ${token.get('liquidity', 0):,.0f}
    """
    
    if is_whitelisted:
        message += "\n⭐ **این توکن توسط یک کیف پول سفید خریداری شده است!**"
    
    if buyers:
        message += "\n\n🐋 **کیف‌پول‌های خریدار اولیه (۵ نفر اول):**\n"
        for i, buyer in enumerate(buyers[:5], 1):
            addr = buyer.get("address", "نامشخص")
            short_addr = addr[:6] + "..." + addr[-4:] if len(addr) > 10 else addr
            amount = buyer.get("amount", 0)
            is_white = "⭐" if addr in whitelist else ""
            message += f"{i}. `{short_addr}` (مقدار: {amount:.2f} توکن) {is_white}\n"
    
    message += f"\n🔗 [مشاهده در DexScreener]({token.get('dex_url', '#')})"
    
    return message

# ==================== تابع اصلی ====================

def main():
    """تابع اصلی ربات - فقط ارزهای با خریدار اولیه"""
    print("\n" + "="*60)
    print(f"⏳ شروع اسکن جدید در {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    start_time = time.time()
    
    # ۱. دریافت از DexScreener
    dex_gainers = get_gainers_from_dex()
    
    if not dex_gainers:
        print("ℹ️ هیچ ارز DEX باکیفیتی پیدا نشد.")
        return
    
    # ۲. مرتب‌سازی بر اساس رشد
    dex_gainers.sort(key=lambda x: float(x.get('change_24h', 0)) if x.get('change_24h') is not None else 0, reverse=True)
    
    # ۳. پیدا کردن خریداران اولیه
    print("\n" + "="*60)
    print("🔍 بررسی خریداران اولیه برای هر ارز")
    print("="*60)
    
    valid_tokens = []
    
    for token in dex_gainers:
        contract = token.get("contract", "")
        chain = token.get("chain", "")
        
        if contract and len(contract) > 10:
            print(f"\n🔍 در حال بررسی {token['symbol']} ({chain})...")
            buyers = get_first_buyers(contract, chain)
            
            if buyers:
                # ذخیره کیف پول‌ها در دیتابیس
                for buyer in buyers:
                    update_wallet(
                        wallet_address=buyer["address"],
                        token_info=token,
                        chain=chain,
                        price=float(token.get("price", 0))
                    )
                valid_tokens.append((token, buyers))
                print(f"✅ {token['symbol']} دارای {len(buyers)} خریدار اولیه است.")
            else:
                print(f"⏭️ {token['symbol']} بدون خریدار اولیه - حذف شد.")
        else:
            print(f"⏭️ {token['symbol']} بدون قرارداد معتبر - حذف شد.")
    
    # ۴. گزارش نهایی
    print("\n" + "="*60)
    print("📊 گزارش نهایی")
    print("="*60)
    
    print(f"📊 تعداد کل ارزهای DEX باکیفیت: {len(dex_gainers)}")
    print(f"✅ تعداد ارزهای با خریدار اولیه: {len(valid_tokens)}")
    print(f"⏭️ تعداد ارزهای بدون خریدار: {len(dex_gainers) - len(valid_tokens)}")
    
    if not valid_tokens:
        print("ℹ️ هیچ ارزی با خریدار اولیه پیدا نشد.")
        return
    
    # نمایش ارزهای برتر
    print("\n🏆 ارزهای برتر با خریدار اولیه:")
    for i, (token, buyers) in enumerate(valid_tokens[:5], 1):
        change = float(token.get('change_24h', 0)) if token.get('change_24h') is not None else 0
        print(f"   {i}. {token['symbol']} ({token['chain']}) - رشد: {change:.2f}% - تعداد خریدار: {len(buyers)}")
    
    # ۵. ارسال گزارش
    report_count = min(CONFIG["REPORT_COUNT"], len(valid_tokens))
    print(f"\n📨 در حال ارسال گزارش برای {report_count} ارز با خریدار اولیه...")
    
    success_count = 0
    for i, (token, buyers) in enumerate(valid_tokens[:report_count], 1):
        print(f"\n--- گزارش {i}: {token['symbol']} ---")
        message = format_message(token, buyers)
        if send_telegram_message(message):
            success_count += 1
            print(f"✅ گزارش {token['symbol']} با موفقیت ارسال شد.")
        else:
            print(f"❌ ارسال گزارش {token['symbol']} ناموفق بود.")
        
        if i < report_count:
            time.sleep(2)
    
    # ۶. گزارش نهایی
    elapsed_time = time.time() - start_time
    print("\n" + "="*60)
    print(f"✅ فرآیند اسکن و گزارش‌دهی در {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} به پایان رسید.")
    print(f"⏱️ زمان اجرا: {elapsed_time:.2f} ثانیه")
    print(f"📊 تعداد کل ارزهای DEX باکیفیت: {len(dex_gainers)}")
    print(f"✅ تعداد ارزهای با خریدار اولیه: {len(valid_tokens)}")
    print(f"📨 تعداد گزارش‌های ارسال شده: {success_count}")
    print("="*60)

if __name__ == "__main__":
    main()
