import requests
import os
import csv
import time
from datetime import datetime
import sys
import json

# ==================== تنظیمات اولیه ====================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY")
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY")  # کلید جدید BSC

if not TELEGRAM_TOKEN or not CHAT_ID or not ETHERSCAN_API_KEY:
    print("❌ خطا: توکن، آیدی کانال یا کلید اتریوم در Secrets تنظیم نشده است.")
    sys.exit(1)

if not BSCSCAN_API_KEY:
    print("⚠️ هشدار: BSCSCAN_API_KEY تنظیم نشده است. شبکه BSC بررسی نمی‌شود.")

# آدرس‌های API
TRENDING_METAS_URL = "https://api.dexscreener.com/metas/trending/v1"
META_DETAILS_URL = "https://api.dexscreener.com/metas/meta/v1"

# ==================== تنظیمات فیلترها ====================
CONFIG = {
    "MIN_LIQUIDITY_DEX": 10000,    # کاهش از ۲۰,۰۰۰ به ۱۰,۰۰۰ دلار
    "MIN_VOLUME_DEX": 1500,        # کاهش از ۳,۰۰۰ به ۱,۵۰۰ دلار
    "MIN_CHANGE_24H": 5,           # کاهش از ۱۰٪ به ۵٪
    "MAX_CHANGE_24H": 500,
    "REPORT_COUNT": 5,
    "MAX_CATEGORIES": 10,
    "SUPPORTED_CHAINS": [
        "ethereum", "eth",
        "bsc", "bnb",
        "polygon",
        "arbitrum",
        "linea",
        "celo",
        "gnosis"
    ]
}

DATA_DIR = "data"
WALLETS_FILE = os.path.join(DATA_DIR, "wallets.csv")
TRADES_FILE = os.path.join(DATA_DIR, "trades.csv")
SELLS_FILE = os.path.join(DATA_DIR, "sells.csv")
WHITELIST_FILE = os.path.join(DATA_DIR, "whitelist.csv")

# ==================== توابع CSV ====================

def ensure_data_dir():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

def read_csv(file_path, headers):
    ensure_data_dir()
    if not os.path.exists(file_path):
        return []
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        return list(reader)

def write_csv(file_path, headers, data):
    ensure_data_dir()
    with open(file_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(data)

def get_wallet(address):
    data = read_csv(WALLETS_FILE, get_wallet_headers())
    for row in data:
        if row.get("address") == address:
            return row
    return None

def update_wallet(wallet_data):
    headers = get_wallet_headers()
    data = read_csv(WALLETS_FILE, headers)
    
    found = False
    for i, row in enumerate(data):
        if row.get("address") == wallet_data["address"]:
            data[i] = wallet_data
            found = True
            break
    
    if not found:
        data.append(wallet_data)
    
    write_csv(WALLETS_FILE, headers, data)

def get_wallet_headers():
    return ["address", "chain", "first_seen", "last_seen", "total_trades",
            "total_sells", "winning_sells", "losing_sells", "win_rate",
            "avg_profit", "avg_hold_duration", "score", "in_whitelist"]

def get_trades_headers():
    return ["trade_id", "wallet_address", "token", "token_name",
            "buy_price", "buy_date", "status", "total_sold_percent", "sell_ids"]

def get_sells_headers():
    return ["sell_id", "trade_id", "wallet_address", "token",
            "sell_price", "sell_date", "sell_percent",
            "profit_percent", "is_winning", "hold_duration_hours"]

def get_whitelist_headers():
    return ["rank", "wallet_address", "chain", "score",
            "total_trades", "win_rate", "avg_profit", "last_seen"]

# ==================== توابع دیتابیس ====================

def add_trade(wallet_address, token_info, price, chain):
    trade_id = f"trade_{int(time.time())}_{wallet_address[:8]}"
    
    trade_data = {
        "trade_id": trade_id,
        "wallet_address": wallet_address,
        "token": token_info.get("symbol", "نامشخص"),
        "token_name": token_info.get("name", "نامشخص"),
        "buy_price": str(price),
        "buy_date": datetime.utcnow().isoformat(),
        "status": "open",
        "total_sold_percent": "0",
        "sell_ids": ""
    }
    
    headers = get_trades_headers()
    data = read_csv(TRADES_FILE, headers)
    data.append(trade_data)
    write_csv(TRADES_FILE, headers, data)
    
    wallet = get_wallet(wallet_address)
    if wallet:
        wallet["total_trades"] = str(int(wallet.get("total_trades", 0)) + 1)
        wallet["last_seen"] = datetime.utcnow().isoformat()
        update_wallet(wallet)
    else:
        new_wallet = {
            "address": wallet_address,
            "chain": chain,
            "first_seen": datetime.utcnow().isoformat(),
            "last_seen": datetime.utcnow().isoformat(),
            "total_trades": "1",
            "total_sells": "0",
            "winning_sells": "0",
            "losing_sells": "0",
            "win_rate": "0",
            "avg_profit": "0",
            "avg_hold_duration": "0",
            "score": "0",
            "in_whitelist": "FALSE"
        }
        update_wallet(new_wallet)
    
    return trade_id

def add_sell(trade_id, wallet_address, token, sell_price, sell_percent, profit_percent, is_winning, hold_duration):
    sell_id = f"sell_{int(time.time())}_{wallet_address[:8]}"
    
    sell_data = {
        "sell_id": sell_id,
        "trade_id": trade_id,
        "wallet_address": wallet_address,
        "token": token,
        "sell_price": str(sell_price),
        "sell_date": datetime.utcnow().isoformat(),
        "sell_percent": str(sell_percent),
        "profit_percent": str(profit_percent),
        "is_winning": "TRUE" if is_winning else "FALSE",
        "hold_duration_hours": str(hold_duration)
    }
    
    headers = get_sells_headers()
    data = read_csv(SELLS_FILE, headers)
    data.append(sell_data)
    write_csv(SELLS_FILE, headers, data)
    
    wallet = get_wallet(wallet_address)
    if wallet:
        wallet["total_sells"] = str(int(wallet.get("total_sells", 0)) + 1)
        if is_winning:
            wallet["winning_sells"] = str(int(wallet.get("winning_sells", 0)) + 1)
        else:
            wallet["losing_sells"] = str(int(wallet.get("losing_sells", 0)) + 1)
        wallet["last_seen"] = datetime.utcnow().isoformat()
        update_wallet(wallet)
    
    trades = read_csv(TRADES_FILE, get_trades_headers())
    for trade in trades:
        if trade.get("trade_id") == trade_id:
            current_sold = float(trade.get("total_sold_percent", 0))
            new_sold = current_sold + sell_percent
            trade["total_sold_percent"] = str(min(new_sold, 100))
            
            sell_ids = trade.get("sell_ids", "")
            if sell_ids:
                trade["sell_ids"] = sell_ids + ";" + sell_id
            else:
                trade["sell_ids"] = sell_id
            
            if new_sold >= 100:
                trade["status"] = "closed"
            else:
                trade["status"] = "partially_sold"
            break
    
    write_csv(TRADES_FILE, get_trades_headers(), trades)

def get_whitelist():
    """دریافت لیست کیف پول‌های سفید"""
    headers = get_whitelist_headers()
    data = read_csv(WHITELIST_FILE, headers)
    return [row.get("wallet_address") for row in data if row]

# ==================== توابع ارسال پیام ====================

def send_telegram_report(token_info, buyers, is_whitelisted=False):
    """ارسال گزارش کامل و مفهومی به تلگرام"""
    
    report = f"""
🦄 **سیگنال معاملاتی - ارز با خریدار اولیه**

━━━━━━━━━━━━━━━━━━━━━
📊 **اطلاعات ارز**
▫️ نام: {token_info.get('name', 'نامشخص')} (${token_info.get('symbol', 'نامشخص')})
▫️ شبکه: {token_info.get('chain', 'نامشخص')}
▫️ دسته‌بندی: {token_info.get('meta_name', 'نامشخص')}
▫️ صرافی: {token_info.get('dex', 'نامشخص')}
▫️ قیمت فعلی: ${float(token_info.get('price', 0)):,.6f}
▫️ رشد ۲۴ ساعته: **{token_info.get('change_24h', 0):.2f}%** ✅
▫️ حجم معاملات: ${token_info.get('volume', 0):,.0f}
▫️ نقدینگی: ${token_info.get('liquidity', 0):,.0f}
▫️ مارکت‌کپ: ${token_info.get('market_cap', 0):,.0f}

━━━━━━━━━━━━━━━━━━━━━
🐋 **کیف‌پول‌های خریدار اولیه ({len(buyers)} نفر)**
"""
    
    whitelist = get_whitelist()
    
    for i, buyer in enumerate(buyers[:5], 1):
        addr = buyer.get("address", "نامشخص")
        short_addr = addr[:8] + "..." + addr[-6:] if len(addr) > 14 else addr
        amount = buyer.get("amount", 0)
        is_white = "⭐ **WHITELIST**" if addr in whitelist else ""
        report += f"{i}. `{short_addr}`\n   └─ مقدار: {amount:.0f} توکن {is_white}\n"
    
    report += f"""
━━━━━━━━━━━━━━━━━━━━━
📈 **تحلیل سریع**
▫️ **امتیاز کیف پول:** {"عالی" if len(buyers) >= 5 else "خوب"}
▫️ **تعداد خریداران اولیه:** {len(buyers)}
▫️ **کیف پول سفید:** {"✅ بله" if is_whitelisted else "❌ خیر"}
▫️ **نقدینگی:** {"✅ مناسب" if token_info.get('liquidity', 0) > 50000 else "⚠️ متوسط"}

🔗 [مشاهده در DexScreener]({token_info.get('dex_url', '#')})
📊 [مشاهده در Etherscan](https://etherscan.io/address/{token_info.get('contract', '')})

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
    
    return report

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

def send_performance_report(summary):
    """ارسال گزارش عملکرد روزانه"""
    message = f"""
📊 **گزارش عملکرد ربات**

━━━━━━━━━━━━━━━━━━━━━
📈 **خلاصه اسکن امروز**
▫️ تعداد کل ارزهای باکیفیت: {summary.get('total_tokens', 0)}
▫️ تعداد ارزهای با خریدار اولیه: {summary.get('valid_tokens', 0)}
▫️ تعداد کیف پول‌های جدید: {summary.get('new_wallets', 0)}
▫️ تعداد کیف پول‌های سفید: {summary.get('whitelist_count', 0)}

⏰ آخرین به‌روزرسانی: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
    send_telegram_message(message)

# ==================== توابع دریافت از DexScreener ====================

def get_trending_metas():
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
    print("="*60)
    print("🚀 [DEX] شروع جستجو در صرافی‌های غیرمتمرکز (گسترش یافته)")
    print("="*60)
    
    metas = get_trending_metas()
    if not metas:
        print("❌ [DEX] هیچ دسته‌بندی داغی پیدا نشد.")
        return []
    
    top_metas = []
    for meta in metas:
        top_metas.append({
            "slug": meta.get("slug"),
            "name": meta.get("name", "نامشخص"),
            "change_24h": meta.get("marketCapChange", {}).get("h24", 0)
        })
    
    print(f"📊 [DEX] تعداد کل دسته‌بندی‌ها: {len(top_metas)}")
    
    if not top_metas:
        print("ℹ️ [DEX] هیچ دسته‌بندی یافت نشد.")
        return []
    
    print("\n🏆 [DEX] دسته‌بندی‌های برتر:")
    sorted_metas = sorted(top_metas, key=lambda x: x["change_24h"], reverse=True)
    for i, meta in enumerate(sorted_metas[:10], 1):
        print(f"   {i}. {meta['name']} (رشد دسته: {meta['change_24h']:.2f}%)")
    
    all_gainers = []
    seen_tokens = set()
    filtered_count = 0
    total_tokens_checked = 0
    
    for meta in sorted_metas[:CONFIG["MAX_CATEGORIES"]]:
        slug = meta["slug"]
        print(f"\n🔎 [DEX] بررسی دسته: {meta['name']} ({slug})")
        
        pairs = get_tokens_from_meta(slug)
        token_count = 0
        
        for pair in pairs:
            total_tokens_checked += 1
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
    
    all_gainers.sort(key=lambda x: float(x.get('change_24h', 0)), reverse=True)
    
    print(f"\n📈 [DEX] تعداد کل ارزهای باکیفیت پیدا شده: {len(all_gainers)}")
    print(f"📊 [DEX] تعداد کل توکن‌های بررسی شده: {total_tokens_checked}")
    print(f"⏭️ [DEX] تعداد ارزهای فیلتر شده: {filtered_count}")
    
    return all_gainers

# ==================== توابع پیدا کردن خریداران اولیه ====================

def get_first_buyers_evm(contract_address, chain_name="ethereum"):
    """پیدا کردن خریداران اولیه با Etherscan API V2"""
    chain_map = {
        "ethereum": 1, "eth": 1,
        "bsc": 56, "bnb": 56,
        "polygon": 137,
        "arbitrum": 42161,
        "linea": 59144,
        "celo": 42220,
        "gnosis": 100
    }
    
    chain_id = chain_map.get(chain_name.lower(), 1)
    
    # انتخاب کلید مناسب بر اساس شبکه
    if chain_name.lower() in ["bsc", "bnb"]:
        api_key = BSCSCAN_API_KEY
        if not api_key:
            print("⚠️ [BSC] کلید BSCscan تنظیم نشده است.")
            return []
    else:
        api_key = ETHERSCAN_API_KEY
    
    url = f"https://api.etherscan.io/v2/api?chainid={chain_id}&module=account&action=tokentx&contractaddress={contract_address}&sort=asc&apikey={api_key}"
    
    print(f"🔗 [EVM] در حال بررسی قرارداد: {contract_address[:10]}...{contract_address[-6:]} در شبکه {chain_name} (Chain ID: {chain_id})")
    
    try:
        response = requests.get(url, timeout=15)
        data = response.json()
        
        if data.get("status") != "1":
            print(f"⚠️ [EVM] خطای API: {data.get('message', data.get('result', 'خطای ناشناخته'))}")
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
    if not contract_address or len(contract_address) < 10:
        print(f"⚠️ آدرس قرارداد نامعتبر")
        return []
    
    chain = chain_name.lower()
    
    if chain in CONFIG["SUPPORTED_CHAINS"]:
        return get_first_buyers_evm(contract_address, chain)
    else:
        print(f"ℹ️ شبکه {chain} پشتیبانی نمی‌شود.")
        return []

# ==================== توابع تشخیص فروش ====================

def check_sell(wallet_address, token, buy_price, buy_date, trade_id):
    """بررسی اینکه آیا کیف پول ارز را فروخته است یا خیر"""
    try:
        url = f"https://api.dexscreener.com/latest/dex/search?q={token}"
        response = requests.get(url, timeout=10)
        data = response.json()
        
        if data.get("pairs"):
            current_price = float(data["pairs"][0].get("priceUsd", 0))
            
            profit_percent = ((current_price - buy_price) / buy_price) * 100
            
            if profit_percent >= 20:
                buy_time = datetime.fromisoformat(buy_date.replace('Z', '+00:00'))
                now = datetime.utcnow()
                hold_duration = (now - buy_time).total_seconds() / 3600
                
                add_sell(
                    trade_id=trade_id,
                    wallet_address=wallet_address,
                    token=token,
                    sell_price=current_price,
                    sell_percent=100,
                    profit_percent=profit_percent,
                    is_winning=True,
                    hold_duration=hold_duration
                )
                return True
    except Exception as e:
        print(f"⚠️ خطا در بررسی فروش برای {token}: {e}")
    
    return False

# ==================== تابع اصلی ====================

def main():
    print("\n" + "="*60)
    print(f"⏳ شروع اسکن جدید در {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)
    
    start_time = time.time()
    
    # ۱. اسکن بازار
    gainers = get_gainers_from_dex()
    
    if not gainers:
        print("ℹ️ هیچ ارز با رشد بالا پیدا نشد.")
        return
    
    # ۲. مرتب‌سازی بر اساس رشد
    gainers.sort(key=lambda x: float(x.get('change_24h', 0)), reverse=True)
    
    # ۳. پیدا کردن خریداران اولیه
    print("\n" + "="*60)
    print("🔍 بررسی خریداران اولیه")
    print("="*60)
    
    valid_tokens = []
    new_wallets_count = 0
    max_tokens_to_check = min(20, len(gainers))
    
    for token in gainers[:max_tokens_to_check]:
        contract = token.get("contract", "")
        chain = token.get("chain", "")
        
        if contract and len(contract) > 10:
            print(f"\n🔍 در حال بررسی {token['symbol']} ({chain})...")
            buyers = get_first_buyers(contract, chain)
            
            if buyers:
                for buyer in buyers:
                    trade_id = add_trade(
                        wallet_address=buyer["address"],
                        token_info=token,
                        price=float(token.get("price", 0)),
                        chain=chain
                    )
                    new_wallets_count += 1
                    
                    check_sell(
                        wallet_address=buyer["address"],
                        token=token.get("symbol", ""),
                        buy_price=float(token.get("price", 0)),
                        buy_date=datetime.utcnow().isoformat(),
                        trade_id=trade_id
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
    
    whitelist = get_whitelist()
    
    print(f"📊 تعداد کل ارزهای باکیفیت: {len(gainers)}")
    print(f"✅ تعداد ارزهای با خریدار اولیه: {len(valid_tokens)}")
    print(f"🆕 کیف پول‌های جدید ذخیره شده: {new_wallets_count}")
    print(f"⭐ تعداد کیف پول‌های سفید: {len(whitelist)}")
    
    # ۵. ارسال گزارش کامل به تلگرام
    report_count = min(5, len(valid_tokens))
    if report_count > 0:
        print(f"\n📨 در حال ارسال {report_count} گزارش کامل به تلگرام...")
        
        for i, (token, buyers) in enumerate(valid_tokens[:report_count], 1):
            is_whitelisted = any(buyer.get("address") in whitelist for buyer in buyers)
            
            report = send_telegram_report(token, buyers, is_whitelisted)
            send_telegram_message(report)
            time.sleep(3)
    
    # ۶. ارسال گزارش عملکرد
    summary = {
        'total_tokens': len(gainers),
        'valid_tokens': len(valid_tokens),
        'new_wallets': new_wallets_count,
        'whitelist_count': len(whitelist)
    }
    
    if len(valid_tokens) > 0:
        send_performance_report(summary)
    
    elapsed_time = time.time() - start_time
    print(f"\n✅ فرآیند در {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} به پایان رسید.")
    print(f"⏱️ زمان اجرا: {elapsed_time:.2f} ثانیه")

if __name__ == "__main__":
    main()
