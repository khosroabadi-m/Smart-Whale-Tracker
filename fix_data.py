import csv
import os
from datetime import datetime

DATA_DIR = "data"
WALLETS_FILE = os.path.join(DATA_DIR, "wallets.csv")
SELLS_FILE = os.path.join(DATA_DIR, "sells.csv")
WHITELIST_FILE = os.path.join(DATA_DIR, "whitelist.csv")

MAX_REASONABLE_PROFIT = 50  # ✅ هماهنگ با monitor_nightly.py
MIN_SCORE_FOR_WHITELIST = 70

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

def fix_sells():
    """اصلاح sells.csv: محدود کردن profit_percent به ۵۰"""
    print("🔄 اصلاح sells.csv...")
    sells = read_csv(SELLS_FILE, ["sell_id", "trade_id", "wallet_address", "token",
                                   "sell_price", "sell_date", "sell_percent",
                                   "profit_percent", "is_winning", "hold_duration_hours"])
    
    fixed_count = 0
    for row in sells:
        try:
            profit = float(row.get("profit_percent", 0))
            if profit > MAX_REASONABLE_PROFIT:
                row["profit_percent"] = str(MAX_REASONABLE_PROFIT)
                fixed_count += 1
        except:
            continue
    
    write_csv(SELLS_FILE, ["sell_id", "trade_id", "wallet_address", "token",
                           "sell_price", "sell_date", "sell_percent",
                           "profit_percent", "is_winning", "hold_duration_hours"], sells)
    print(f"✅ {fixed_count} سود غیرطبیعی اصلاح شد.")

def calculate_score(wallet, sells):
    total_trades = int(wallet.get("total_trades", 0))
    total_sells = int(wallet.get("total_sells", 0))
    winning_sells = int(wallet.get("winning_sells", 0))
    
    if total_sells == 0:
        return 0.0, 0, 0, 0
    
    wallet_sells = [s for s in sells if s.get("wallet_address") == wallet.get("address")]
    total_profit = sum(float(s.get("profit_percent", 0)) for s in wallet_sells)
    avg_profit = total_profit / total_sells if total_sells > 0 else 0
    
    if avg_profit > MAX_REASONABLE_PROFIT:
        avg_profit = MAX_REASONABLE_PROFIT
    
    win_rate = (winning_sells / total_sells) * 100
    
    total_duration = sum(float(s.get("hold_duration_hours", 0)) for s in wallet_sells)
    avg_duration = total_duration / total_sells if total_sells > 0 else 0
    
    partial_sells = sum(1 for s in wallet_sells if float(s.get("sell_percent", 0)) < 100)
    capital_management_score = (partial_sells / total_trades) * 10 if total_trades > 0 else 0
    
    timing_score = (avg_profit / (avg_duration + 1)) * 10 if avg_duration >= 0 else 0
    
    # وزن‌های جدید
    score = (win_rate * 0.55) + (avg_profit * 0.20) + (timing_score * 0.15) + (capital_management_score * 0.10)
    
    return round(score, 2), avg_profit, win_rate, avg_duration

def fix_wallets():
    """به‌روزرسانی wallets.csv و whitelist.csv"""
    print("🔄 به‌روزرسانی wallets.csv...")
    
    wallets = read_csv(WALLETS_FILE, ["address", "chain", "first_seen", "last_seen", "total_trades",
                                       "total_sells", "winning_sells", "losing_sells", "win_rate",
                                       "avg_profit", "avg_hold_duration", "score", "in_whitelist"])
    
    sells = read_csv(SELLS_FILE, ["wallet_address", "profit_percent", "sell_percent", "hold_duration_hours"])
    
    updated_count = 0
    for wallet in wallets:
        score, avg_profit, win_rate, avg_duration = calculate_score(wallet, sells)
        wallet["score"] = str(score)
        wallet["avg_profit"] = str(avg_profit)
        wallet["win_rate"] = str(win_rate)
        wallet["avg_hold_duration"] = str(avg_duration)
        
        if score >= MIN_SCORE_FOR_WHITELIST and int(wallet.get("total_trades", 0)) >= 5:
            wallet["in_whitelist"] = "TRUE"
        else:
            wallet["in_whitelist"] = "FALSE"
        
        updated_count += 1
    
    write_csv(WALLETS_FILE, ["address", "chain", "first_seen", "last_seen", "total_trades",
                             "total_sells", "winning_sells", "losing_sells", "win_rate",
                             "avg_profit", "avg_hold_duration", "score", "in_whitelist"], wallets)
    print(f"✅ {updated_count} کیف پول به‌روزرسانی شد.")
    
    # به‌روزرسانی whitelist
    print("🔄 به‌روزرسانی whitelist.csv...")
    whitelist_wallets = [w for w in wallets if w.get("in_whitelist") == "TRUE"]
    whitelist_wallets.sort(key=lambda x: float(x.get("score", 0)), reverse=True)
    
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
    
    write_csv(WHITELIST_FILE, ["rank", "wallet_address", "chain", "score",
                               "total_trades", "win_rate", "avg_profit", "last_seen"], whitelist_data)
    print(f"✅ لیست سفید با {len(whitelist_data)} کیف پول به‌روز شد.")

def main():
    print("="*60)
    print("🛠️ شروع اصلاح داده‌های موجود (با پارامترهای جدید)")
    print("="*60)
    
    # ۱. اصلاح sells.csv
    fix_sells()
    
    # ۲. به‌روزرسانی wallets و whitelist
    fix_wallets()
    
    print("\n✅ فرآیند اصلاح داده‌ها با موفقیت به پایان رسید.")

if __name__ == "__main__":
    main()
