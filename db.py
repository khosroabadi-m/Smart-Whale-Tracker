"""
CSV-backed storage with atomic writes and basic integrity helpers.
"""
import csv
import os
import tempfile
import shutil
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any

import config as cfg


def ensure_data_dir() -> None:
    os.makedirs(cfg.DATA_DIR, exist_ok=True)


def _atomic_write_csv(file_path: str, headers: List[str], rows: List[Dict]) -> None:
    """Write CSV atomically to avoid partial/corrupt files."""
    ensure_data_dir()
    dir_name = os.path.dirname(file_path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".csv", dir=dir_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                # ensure all headers present
                clean = {h: row.get(h, "") for h in headers}
                writer.writerow(clean)
        shutil.move(tmp_path, file_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def read_csv(file_path: str, headers: Optional[List[str]] = None) -> List[Dict[str, str]]:
    ensure_data_dir()
    if not os.path.exists(file_path):
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if headers:
        # normalize missing keys
        for r in rows:
            for h in headers:
                r.setdefault(h, "")
    return rows


def write_csv(file_path: str, headers: List[str], data: List[Dict]) -> None:
    _atomic_write_csv(file_path, headers, data)


# ---------- Headers ----------

def wallet_headers() -> List[str]:
    return [
        "address", "chain", "first_seen", "last_seen",
        "total_trades", "total_sells", "winning_sells", "losing_sells",
        "win_rate", "avg_profit", "avg_hold_duration", "score", "in_whitelist",
    ]


def trade_headers() -> List[str]:
    return [
        "trade_id", "wallet_address", "token", "token_name", "contract",
        "chain", "buy_price", "buy_date", "status",
        "total_sold_percent", "sell_ids",
    ]


def sell_headers() -> List[str]:
    return [
        "sell_id", "trade_id", "wallet_address", "token", "contract",
        "sell_price", "sell_date", "sell_percent",
        "profit_percent", "is_winning", "hold_duration_hours", "verified_onchain",
    ]


def whitelist_headers() -> List[str]:
    return [
        "rank", "wallet_address", "chain", "score",
        "total_trades", "win_rate", "avg_profit", "last_seen",
    ]


# ---------- Wallet helpers ----------

def get_wallet(address: str) -> Optional[Dict]:
    address = address.lower()
    for row in read_csv(cfg.WALLETS_FILE, wallet_headers()):
        if row.get("address", "").lower() == address:
            return row
    return None


def upsert_wallet(wallet_data: Dict) -> None:
    headers = wallet_headers()
    data = read_csv(cfg.WALLETS_FILE, headers)
    addr = wallet_data["address"].lower()
    wallet_data["address"] = addr
    found = False
    for i, row in enumerate(data):
        if row.get("address", "").lower() == addr:
            data[i] = {**row, **wallet_data}
            found = True
            break
    if not found:
        data.append(wallet_data)
    write_csv(cfg.WALLETS_FILE, headers, data)


def is_blacklisted(address: str) -> bool:
    return address.lower() in {a.lower() for a in cfg.BLACKLIST_ADDRESSES}


# ---------- Trade helpers ----------

def trade_exists(wallet: str, contract: str, within_hours: float = 24.0) -> bool:
    """Avoid recording the same wallet+token many times in a short window."""
    wallet = wallet.lower()
    contract = (contract or "").lower()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for t in read_csv(cfg.TRADES_FILE, trade_headers()):
        if t.get("wallet_address", "").lower() != wallet:
            continue
        if (t.get("contract") or "").lower() != contract:
            continue
        try:
            buy_dt = datetime.fromisoformat(t["buy_date"].replace("Z", "+00:00").split("+")[0])
            hours = (now - buy_dt).total_seconds() / 3600
            if hours < within_hours:
                return True
        except Exception:
            continue
    return False


def add_trade(
    wallet_address: str,
    token_info: Dict,
    price: float,
    chain: str,
) -> Optional[str]:
    wallet_address = wallet_address.lower()
    if is_blacklisted(wallet_address):
        return None

    contract = (token_info.get("contract") or "").lower()
    if trade_exists(wallet_address, contract, within_hours=48.0):
        return None  # already tracked recently

    trade_id = f"trade_{int(datetime.now(timezone.utc).replace(tzinfo=None).timestamp())}_{wallet_address[:10]}"
    trade_data = {
        "trade_id": trade_id,
        "wallet_address": wallet_address,
        "token": token_info.get("symbol", "UNKNOWN")[:32],
        "token_name": (token_info.get("name") or "UNKNOWN")[:64],
        "contract": contract,
        "chain": chain.lower(),
        "buy_price": f"{price:.10f}".rstrip("0").rstrip("."),
        "buy_date": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "status": "open",
        "total_sold_percent": "0",
        "sell_ids": "",
    }

    headers = trade_headers()
    data = read_csv(cfg.TRADES_FILE, headers)
    data.append(trade_data)
    write_csv(cfg.TRADES_FILE, headers, data)

    # update / create wallet
    wallet = get_wallet(wallet_address)
    now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    if wallet:
        wallet["total_trades"] = str(int(wallet.get("total_trades") or 0) + 1)
        wallet["last_seen"] = now_iso
        if chain:
            wallet["chain"] = chain.lower()
        upsert_wallet(wallet)
    else:
        upsert_wallet({
            "address": wallet_address,
            "chain": chain.lower(),
            "first_seen": now_iso,
            "last_seen": now_iso,
            "total_trades": "1",
            "total_sells": "0",
            "winning_sells": "0",
            "losing_sells": "0",
            "win_rate": "0",
            "avg_profit": "0",
            "avg_hold_duration": "0",
            "score": "0",
            "in_whitelist": "FALSE",
        })
    return trade_id


def add_sell(
    trade_id: str,
    wallet_address: str,
    token: str,
    contract: str,
    sell_price: float,
    sell_percent: float,
    profit_percent: float,
    is_winning: bool,
    hold_duration: float,
    verified_onchain: bool = False,
) -> Optional[str]:
    wallet_address = wallet_address.lower()
    if is_blacklisted(wallet_address):
        return None

    # Cap extreme profits for storage consistency
    if profit_percent > cfg.MAX_REASONABLE_PROFIT:
        profit_percent = cfg.MAX_REASONABLE_PROFIT
    if profit_percent < -95:
        profit_percent = -95.0

    sell_id = f"sell_{int(datetime.now(timezone.utc).replace(tzinfo=None).timestamp())}_{wallet_address[:10]}"
    sell_data = {
        "sell_id": sell_id,
        "trade_id": trade_id,
        "wallet_address": wallet_address,
        "token": token[:32],
        "contract": (contract or "").lower(),
        "sell_price": f"{sell_price:.10f}".rstrip("0").rstrip("."),
        "sell_date": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "sell_percent": str(round(sell_percent, 2)),
        "profit_percent": str(round(profit_percent, 2)),
        "is_winning": "TRUE" if is_winning else "FALSE",
        "hold_duration_hours": str(round(hold_duration, 4)),
        "verified_onchain": "TRUE" if verified_onchain else "FALSE",
    }

    headers = sell_headers()
    data = read_csv(cfg.SELLS_FILE, headers)
    # prevent exact duplicate sells for same trade
    for s in data:
        if s.get("trade_id") == trade_id and s.get("wallet_address", "").lower() == wallet_address:
            # already has a sell for this trade – skip
            return None
    data.append(sell_data)
    write_csv(cfg.SELLS_FILE, headers, data)

    # update wallet counters
    wallet = get_wallet(wallet_address)
    if wallet:
        wallet["total_sells"] = str(int(wallet.get("total_sells") or 0) + 1)
        if is_winning:
            wallet["winning_sells"] = str(int(wallet.get("winning_sells") or 0) + 1)
        else:
            wallet["losing_sells"] = str(int(wallet.get("losing_sells") or 0) + 1)
        wallet["last_seen"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        upsert_wallet(wallet)

    # update trade status
    trades = read_csv(cfg.TRADES_FILE, trade_headers())
    for trade in trades:
        if trade.get("trade_id") == trade_id:
            current = float(trade.get("total_sold_percent") or 0)
            new_sold = min(current + sell_percent, 100.0)
            trade["total_sold_percent"] = str(new_sold)
            sids = trade.get("sell_ids") or ""
            trade["sell_ids"] = (sids + ";" + sell_id).strip(";")
            trade["status"] = "closed" if new_sold >= 99.0 else "partially_sold"
            break
    write_csv(cfg.TRADES_FILE, trade_headers(), trades)
    return sell_id


def get_open_trades() -> List[Dict]:
    return [
        t for t in read_csv(cfg.TRADES_FILE, trade_headers())
        if t.get("status") in ("open", "partially_sold")
    ]


def get_whitelist_addresses() -> set:
    return {
        row.get("wallet_address", "").lower()
        for row in read_csv(cfg.WHITELIST_FILE, whitelist_headers())
        if row.get("wallet_address")
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
