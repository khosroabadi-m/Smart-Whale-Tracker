import csv
import os
import glob
import time
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Dict, Set

# تنظیم لاگینگ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("data/fix_data.log", encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# مسیرها
DATA_DIR = "data"
TRADES_FILE = os.path.join(DATA_DIR, "trades.csv")
WALLETS_FILE = os.path.join(DATA_DIR, "wallets.csv")
SELLES_FILE = os.path.join(DATA_DIR, "sells.csv")
LOGS_DIR = os.path.join(DATA_DIR, "logs")

def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)

def load_csv(filename: str) -> List[Dict]:
    if not os.path.exists(filename):
        return []
    with open(filename, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))

def save_csv(filename: str, rows: List[Dict]):
    if not rows:
        if os.path.exists(filename):
            os.remove(filename)
        return
    headers = rows[0].keys()
    with open(filename, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

def sanitize():
    """پاکسازی اصلی داده‌ها"""
    trades = load_csv(TRADES_FILE)
    wallets = load_csv(WALLETS_FILE)

    logger.info(f"Loading {len(trades)} trades and {len(wallets)} wallets...")

    # حذف tradeهای بدون contract
    trades_with_contract = [t for t in trades if t.get('contract') and t.get('contract').startswith('0x')]
    trades_no_contract = len(trades) - len(trades_with_contract)

    # deduplication open trades (فقط جدیدترین را نگه دار)
    dup_groups: Dict[str, List[Dict]] = defaultdict(list)
    for t in trades_with_contract:
        key = f"{t['wallet_address']}_{t.get('contract')}"
        dup_groups[key].append(t)

    dup_open = 0
    deduped_trades = []
    for group in dup_groups.values():
        # جدیدترین را نگه دار
        group_sorted = sorted(group, key=lambda x: x.get('buy_date', ''), reverse=True)
        deduped_trades.append(group_sorted[0])
        dup_open += len(group) - 1

    trades_removed = len(trades) - len(deduped_trades)
    trades_dup_open = dup_open

    # ذخیره tradeهای تمیز
    save_csv(TRADES_FILE, deduped_trades)

    # بازنویسی wallets.csv با تعداد معامله‌های واقعی
    wallet_stats = {}
    for t in deduped_trades:
        addr = t['wallet_address']
        wallet_stats[addr] = wallet_stats.get(addr, 0) + 1

    new_wallets = []
    for w in wallets:
        addr = w['address']
        w['total_trades'] = str(wallet_stats.get(addr, 0))
        new_wallets.append(w)

    save_csv(WALLETS_FILE, new_wallets)

    # لاگ نهایی
    logger.info(f"Sanitize done: {trades_removed} trades removed, {trades_no_contract} no contract, {trades_dup_open} dup open")
    logger.info(f"Trades: {len(trades)} → {len(deduped_trades)} (with_contract={len(deduped_trades)})")
    logger.info(f"Wallets: {len(wallets)}")

def main():
    ensure_dirs()
    start = time.time()
    sanitize()
    end = time.time()
    logger.info(f"✅ Fix historical data finished in {end - start:.1f}s")

if __name__ == "__main__":
    main()
