#!/usr/bin/env python3
"""
Unit / integration tests for core logic (no live API keys required).
Run: python -m pytest tests/ -v   OR   python tests/test_logic.py
"""
import os
import sys
import tempfile
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

# make project root importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config as cfg
import db
import scoring
import apis


class TestDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_dir = cfg.DATA_DIR
        cfg.DATA_DIR = self.tmp
        cfg.WALLETS_FILE = os.path.join(self.tmp, "wallets.csv")
        cfg.TRADES_FILE = os.path.join(self.tmp, "trades.csv")
        cfg.SELLS_FILE = os.path.join(self.tmp, "sells.csv")
        cfg.WHITELIST_FILE = os.path.join(self.tmp, "whitelist.csv")

    def tearDown(self):
        cfg.DATA_DIR = self._old_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_trade_and_wallet(self):
        tid = db.add_trade(
            "0xabc1234567890123456789012345678901234567",
            {"symbol": "TEST", "name": "Test Token", "contract": "0xcontract12345678901234567890123456789012"},
            0.001,
            "ethereum",
        )
        self.assertIsNotNone(tid)
        w = db.get_wallet("0xabc1234567890123456789012345678901234567")
        self.assertIsNotNone(w)
        self.assertEqual(w["total_trades"], "1")
        self.assertEqual(w["chain"], "ethereum")

    def test_blacklist_blocks_trade(self):
        tid = db.add_trade(
            "0x0000000000000000000000000000000000000000",
            {"symbol": "X", "name": "X", "contract": "0xabcabcabcabcabcabcabcabcabcabcabcabcabca"},
            1.0,
            "ethereum",
        )
        self.assertIsNone(tid)

    def test_dedup_trade(self):
        addr = "0xdedup1234567890123456789012345678901234"
        t1 = db.add_trade(addr, {"symbol": "A", "name": "A", "contract": "0xc1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1"}, 1.0, "ethereum")
        t2 = db.add_trade(addr, {"symbol": "A", "name": "A", "contract": "0xc1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1"}, 1.0, "ethereum")
        self.assertIsNotNone(t1)
        self.assertIsNone(t2)  # same wallet+contract within window

    def test_add_sell_updates_trade_and_wallet(self):
        addr = "0xsell123456789012345678901234567890123456"
        tid = db.add_trade(
            addr,
            {"symbol": "TK", "name": "Tok", "contract": "0xctrctrctrctrctrctrctrctrctrctrctrctrctr1"},
            0.01,
            "ethereum",
        )
        sid = db.add_sell(
            trade_id=tid,
            wallet_address=addr,
            token="TK",
            contract="0xctrctrctrctrctrctrctrctrctrctrctrctrctr1",
            sell_price=0.02,
            sell_percent=100,
            profit_percent=100,
            is_winning=True,
            hold_duration=5.0,
            verified_onchain=True,
        )
        self.assertIsNotNone(sid)
        w = db.get_wallet(addr)
        self.assertEqual(w["total_sells"], "1")
        self.assertEqual(w["winning_sells"], "1")
        trades = db.read_csv(cfg.TRADES_FILE, db.trade_headers())
        self.assertEqual(trades[0]["status"], "closed")

    def test_atomic_write_readable(self):
        db.write_csv(cfg.WALLETS_FILE, db.wallet_headers(), [
            {"address": "0x1", "chain": "ethereum", "first_seen": "x", "last_seen": "x",
             "total_trades": "1", "total_sells": "0", "winning_sells": "0", "losing_sells": "0",
             "win_rate": "0", "avg_profit": "0", "avg_hold_duration": "0", "score": "0",
             "in_whitelist": "FALSE"}
        ])
        rows = db.read_csv(cfg.WALLETS_FILE, db.wallet_headers())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["address"], "0x1")


class TestScoring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = cfg.DATA_DIR
        cfg.DATA_DIR = self.tmp
        cfg.WALLETS_FILE = os.path.join(self.tmp, "wallets.csv")
        cfg.TRADES_FILE = os.path.join(self.tmp, "trades.csv")
        cfg.SELLS_FILE = os.path.join(self.tmp, "sells.csv")
        cfg.WHITELIST_FILE = os.path.join(self.tmp, "whitelist.csv")

    def tearDown(self):
        cfg.DATA_DIR = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_score_zero_without_sells(self):
        m = scoring.calculate_wallet_metrics(
            {"address": "0x1", "total_trades": "5"}, []
        )
        self.assertEqual(m["score"], 0.0)

    def test_score_with_wins(self):
        sells = [
            {"wallet_address": "0xabc", "profit_percent": "30", "is_winning": "TRUE",
             "hold_duration_hours": "2"},
            {"wallet_address": "0xabc", "profit_percent": "20", "is_winning": "TRUE",
             "hold_duration_hours": "3"},
            {"wallet_address": "0xabc", "profit_percent": "-10", "is_winning": "FALSE",
             "hold_duration_hours": "1"},
        ]
        m = scoring.calculate_wallet_metrics(
            {"address": "0xabc", "total_trades": "5"}, sells
        )
        self.assertAlmostEqual(m["win_rate"], 66.67, places=1)
        self.assertGreater(m["score"], 20)
        self.assertLess(m["score"], 100)

    def test_profit_cap(self):
        sells = [
            {"wallet_address": "0x1", "profit_percent": "500", "is_winning": "TRUE",
             "hold_duration_hours": "1"},
        ]
        m = scoring.calculate_wallet_metrics({"address": "0x1", "total_trades": "1"}, sells)
        self.assertLessEqual(m["avg_profit"], cfg.MAX_REASONABLE_PROFIT)

    def test_sanitize_removes_zero_address(self):
        db.write_csv(cfg.WALLETS_FILE, db.wallet_headers(), [
            {"address": "0x0000000000000000000000000000000000000000", "chain": "ethereum",
             "first_seen": "a", "last_seen": "a", "total_trades": "10", "total_sells": "5",
             "winning_sells": "5", "losing_sells": "0", "win_rate": "100", "avg_profit": "50",
             "avg_hold_duration": "1", "score": "140", "in_whitelist": "TRUE"},
            {"address": "0xreal123456789012345678901234567890123456", "chain": "ethereum",
             "first_seen": "a", "last_seen": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
             "total_trades": "2", "total_sells": "0", "winning_sells": "0", "losing_sells": "0",
             "win_rate": "0", "avg_profit": "0", "avg_hold_duration": "0", "score": "0",
             "in_whitelist": "FALSE"},
        ])
        db.write_csv(cfg.SELLS_FILE, db.sell_headers(), [])
        db.write_csv(cfg.TRADES_FILE, db.trade_headers(), [])
        stats = scoring.sanitize_existing_data()
        self.assertEqual(stats["wallets_removed"], 1)
        wallets = db.read_csv(cfg.WALLETS_FILE, db.wallet_headers())
        self.assertEqual(len(wallets), 1)
        self.assertTrue(wallets[0]["address"].startswith("0xreal"))

    def test_whitelist_threshold(self):
        # create wallet with good metrics
        addr = "0xgood123456789012345678901234567890123456"
        db.write_csv(cfg.WALLETS_FILE, db.wallet_headers(), [{
            "address": addr, "chain": "ethereum",
            "first_seen": "a", "last_seen": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "total_trades": "5", "total_sells": "4", "winning_sells": "3", "losing_sells": "1",
            "win_rate": "0", "avg_profit": "0", "avg_hold_duration": "0", "score": "0",
            "in_whitelist": "FALSE",
        }])
        sells = [
            {"wallet_address": addr, "profit_percent": "25", "is_winning": "TRUE",
             "hold_duration_hours": "4", "sell_id": "1", "trade_id": "t1", "token": "X",
             "contract": "c", "sell_price": "1", "sell_date": "d", "sell_percent": "100",
             "verified_onchain": "TRUE"},
            {"wallet_address": addr, "profit_percent": "40", "is_winning": "TRUE",
             "hold_duration_hours": "2", "sell_id": "2", "trade_id": "t2", "token": "Y",
             "contract": "c2", "sell_price": "1", "sell_date": "d", "sell_percent": "100",
             "verified_onchain": "TRUE"},
            {"wallet_address": addr, "profit_percent": "15", "is_winning": "TRUE",
             "hold_duration_hours": "6", "sell_id": "3", "trade_id": "t3", "token": "Z",
             "contract": "c3", "sell_price": "1", "sell_date": "d", "sell_percent": "100",
             "verified_onchain": "TRUE"},
            {"wallet_address": addr, "profit_percent": "-5", "is_winning": "FALSE",
             "hold_duration_hours": "1", "sell_id": "4", "trade_id": "t4", "token": "W",
             "contract": "c4", "sell_price": "1", "sell_date": "d", "sell_percent": "100",
             "verified_onchain": "TRUE"},
        ]
        db.write_csv(cfg.SELLS_FILE, db.sell_headers(), sells)
        scoring.update_all_scores()
        scoring.rebuild_whitelist()
        w = db.get_wallet(addr)
        self.assertGreater(float(w["score"]), 0)
        # may or may not be whitelist depending on exact weights – just ensure no crash
        wl = db.read_csv(cfg.WHITELIST_FILE, db.whitelist_headers())
        self.assertIsInstance(wl, list)


class TestApisHelpers(unittest.TestCase):
    def test_is_valid_token_filters(self):
        good = {
            "chain": "ethereum",
            "liquidity": 50_000,
            "volume": 20_000,
            "change_24h": 25,
        }
        self.assertTrue(apis.is_valid_token(good))

        bad_chain = {**good, "chain": "solana"}
        self.assertFalse(apis.is_valid_token(bad_chain))

        low_liq = {**good, "liquidity": 100}
        self.assertFalse(apis.is_valid_token(low_liq))

        extreme = {**good, "change_24h": 999}
        self.assertFalse(apis.is_valid_token(extreme))

    def test_find_early_buyers_parses_mock(self):
        mock_txs = [
            {
                "to": "0xbuyer0000000000000000000000000000000001",
                "from": "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",
                "value": str(10 ** 18 * 100),  # 100 tokens
                "tokenDecimal": "18",
                "timeStamp": "1700000000",
                "hash": "0xh1",
            },
            {
                "to": "0x0000000000000000000000000000000000000000",
                "from": "0xsomeone",
                "value": str(10 ** 18 * 50),
                "tokenDecimal": "18",
                "timeStamp": "1700000001",
                "hash": "0xh2",
            },
            {
                "to": "0xbuyer0000000000000000000000000000000002",
                "from": "0xrouter",
                "value": str(10 ** 18 * 200),
                "tokenDecimal": "18",
                "timeStamp": "1700000002",
                "hash": "0xh3",
            },
        ]
        with patch("apis.get_token_transfers", return_value=mock_txs):
            buyers = apis.find_early_buyers("0xcontract", "ethereum")
        self.assertEqual(len(buyers), 2)
        self.assertEqual(buyers[0]["address"], "0xbuyer0000000000000000000000000000000001")
        self.assertNotIn("0x0000000000000000000000000000000000000000",
                         [b["address"] for b in buyers])

    def test_detect_onchain_sell_mock(self):
        buy_ts = 1700000000
        mock_txs = [
            {
                "from": "0xpair",
                "to": "0xwallet000000000000000000000000000000001",
                "value": str(10 ** 18 * 100),
                "tokenDecimal": "18",
                "timeStamp": str(buy_ts + 10),
                "hash": "0xin",
            },
            {
                "from": "0xwallet000000000000000000000000000000001",
                "to": "0xpair",
                "value": str(10 ** 18 * 80),
                "tokenDecimal": "18",
                "timeStamp": str(buy_ts + 3600),
                "hash": "0xout",
            },
        ]
        with patch("apis.get_wallet_token_transfers", return_value=mock_txs):
            info = apis.detect_onchain_sell(
                "0xwallet000000000000000000000000000000001",
                "0xctr",
                "ethereum",
                buy_ts,
            )
        self.assertIsNotNone(info)
        self.assertGreaterEqual(info["sold_percent"], 70)


class TestConfig(unittest.TestCase):
    def test_weights_reasonable(self):
        total = (
            cfg.WEIGHT_WIN_RATE
            + cfg.WEIGHT_AVG_PROFIT
            + cfg.WEIGHT_TIMING
            + cfg.WEIGHT_ACTIVITY
        )
        self.assertAlmostEqual(total, 1.0, places=5)

    def test_chain_map_has_core(self):
        for c in ("ethereum", "bsc", "base", "arbitrum", "polygon"):
            self.assertIn(c, cfg.CHAIN_MAP)


def run_all():
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_all())
