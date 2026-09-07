#!/usr/bin/env python3
"""
One-time migration for data/nightly_log.csv.

The file was created by an older monitor_nightly.py whose header lacked the
`no_sell_after_buy` column (15 cols). The current code appends 16-field rows,
so every csv.DictReader shifts values by one position after
`api_empty_transfers` ("errors" displayed no_sell counts, "notes" displayed
whale_events, and the real notes landed in an unnamed column).

This script rewrites the file with the current header and correctly aligned
rows. Idempotent: running it again on an already-correct file is a no-op.
"""
import csv
import os
import shutil
import sys

# Must match monitor_nightly._nightly_log_headers()
NEW_HEADERS = [
    "run_id", "started_at", "finished_at",
    "open_with_contract", "checked", "skipped_no_contract",
    "sell_detected", "sell_recorded", "sell_no_price", "sell_below_threshold",
    "api_empty_transfers", "no_sell_after_buy", "errors",
    "new_whales", "whale_events", "notes",
]
OLD_HEADERS = [h for h in NEW_HEADERS if h != "no_sell_after_buy"]  # 15 cols

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nightly_log.csv")


def realign_row(row):
    """Map one raw CSV row onto NEW_HEADERS by its field count."""
    if len(row) == len(NEW_HEADERS):
        return dict(zip(NEW_HEADERS, row))
    if len(row) == len(OLD_HEADERS):
        d = dict(zip(OLD_HEADERS, row))
        d["no_sell_after_buy"] = ""
        return d
    d = {h: "" for h in NEW_HEADERS}
    d["run_id"] = row[0] if row else ""
    d["notes"] = "unmigrated: " + ",".join(row)
    return d


def main() -> int:
    if not os.path.exists(PATH):
        print("no nightly_log.csv found — nothing to do")
        return 0

    with open(PATH, "r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        print("nightly_log.csv is empty — nothing to do")
        return 0

    header, body = rows[0], [r for r in rows[1:] if r]
    if header == NEW_HEADERS and all(len(r) == len(NEW_HEADERS) for r in body):
        print("header already correct — nothing to do")
        return 0

    fixed = [realign_row(r) for r in body]
    tmp = PATH + ".migrating"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=NEW_HEADERS)
        w.writeheader()
        w.writerows(fixed)
    shutil.move(tmp, PATH)

    print(f"migrated {len(fixed)} rows: header {len(header)} cols -> {len(NEW_HEADERS)} cols")
    for r in fixed[-3:]:
        print(
            f"  {r['run_id']}: checked={r['checked']} no_sell={r['no_sell_after_buy']} "
            f"errors={r['errors']} new_whales={r['new_whales']} whale_events={r['whale_events']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
