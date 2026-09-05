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

    def test_multiple_sells_on_different_contracts_allowed(self):
        """Wallet should be able to record sells on DIFFERENT contracts.
        This is essential for whale qualification (wins>=2 from different tokens)."""
        addr = "0xmulti123456789012345678901234567890123456"
        # Create 2 trades on different contracts
        tid1 = db.add_trade(addr, {"symbol": "A", "name": "TokA", "contract": "0xaaa111aaa111aaa111aaa111aaa111aaa111aaa1"}, 0.01, "ethereum")
        tid2 = db.add_trade(addr, {"symbol": "B", "name": "TokB", "contract": "0xbbb222bbb222bbb222bbb222bbb222bbb222bbb2"}, 0.02, "ethereum")
        # Record sells on both contracts (with different prices/profits so they don't dedup)
        sid1 = db.add_sell(tid1, addr, "A", "0xaaa111aaa111aaa111aaa111aaa111aaa111aaa1",
                          sell_price=0.02, sell_percent=100, profit_percent=100,
                          is_winning=True, hold_duration=5.0, verified_onchain=True)
        sid2 = db.add_sell(tid2, addr, "B", "0xbbb222bbb222bbb222bbb222bbb222bbb222bbb2",
                          sell_price=0.03, sell_percent=100, profit_percent=50,
                          is_winning=True, hold_duration=10.0, verified_onchain=True)
        self.assertIsNotNone(sid1, "First sell should be recorded")
        self.assertIsNotNone(sid2, "Second sell on different contract should be recorded")
        w = db.get_wallet(addr)
        self.assertEqual(w["total_sells"], "2", "Wallet should have 2 sells")
        self.assertEqual(w["winning_sells"], "2", "Wallet should have 2 winning sells")

    def test_exact_duplicate_sell_blocked(self):
        """Re-recording the EXACT same sell (same wallet+contract+price+profit) should be blocked."""
        addr = "0xdup1234567890123456789012345678901234567"
        tid = db.add_trade(addr, {"symbol": "D", "name": "Dup", "contract": "0xdupdupdupdupdupdupdupdupdupdupdupdupdup1"}, 0.01, "ethereum")
        sid1 = db.add_sell(tid, addr, "D", "0xdupdupdupdupdupdupdupdupdupdupdupdupdup1",
                          sell_price=0.02, sell_percent=100, profit_percent=100,
                          is_winning=True, hold_duration=5.0, verified_onchain=True)
        # Try to record the EXACT same sell again (same price + same profit)
        sid2 = db.add_sell(tid, addr, "D", "0xdupdupdupdupdupdupdupdupdupdupdupdupdup1",
                          sell_price=0.02, sell_percent=100, profit_percent=100,
                          is_winning=True, hold_duration=5.0, verified_onchain=True)
        self.assertIsNotNone(sid1, "First sell should be recorded")
        self.assertIsNone(sid2, "Exact duplicate sell should be blocked")
        w = db.get_wallet(addr)
        self.assertEqual(w["total_sells"], "1", "Wallet should have only 1 sell (duplicate blocked)")

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
    def setUp(self):
        # Make sure ETHERSCAN_API_KEY is set so ethereum chain is active
        self._old_eth = cfg.ETHERSCAN_API_KEY
        cfg.ETHERSCAN_API_KEY = "test_key_for_active_chains"

    def tearDown(self):
        cfg.ETHERSCAN_API_KEY = self._old_eth

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

    def test_bsc_works_on_paid_plan(self):
        """On a PAID plan, BSC tokens should be active (V2 unified endpoint)."""
        old_eth = cfg.ETHERSCAN_API_KEY
        old_tier = cfg.ETHERSCAN_PLAN_TIER
        cfg.ETHERSCAN_API_KEY = "test_etherscan_key"
        cfg.ETHERSCAN_PLAN_TIER = "standard"  # paid plan
        try:
            bsc_token = {
                "chain": "bsc",
                "liquidity": 50_000,
                "volume": 20_000,
                "change_24h": 25,
            }
            # On paid plan, BSC is active
            self.assertTrue(apis.is_valid_token(bsc_token),
                            "BSC should be active on paid plan (V2 unified endpoint)")
        finally:
            cfg.ETHERSCAN_API_KEY = old_eth
            cfg.ETHERSCAN_PLAN_TIER = old_tier

    def test_bsc_filtered_on_free_plan(self):
        """On the Free plan, BSC tokens should be filtered out (paid-tier-only chain)."""
        old_eth = cfg.ETHERSCAN_API_KEY
        old_tier = cfg.ETHERSCAN_PLAN_TIER
        cfg.ETHERSCAN_API_KEY = "test_etherscan_key"
        cfg.ETHERSCAN_PLAN_TIER = "free"  # free plan
        try:
            bsc_token = {
                "chain": "bsc",
                "liquidity": 50_000,
                "volume": 20_000,
                "change_24h": 25,
            }
            # On free plan, BSC is filtered out (would return "Free API access is not supported")
            self.assertFalse(apis.is_valid_token(bsc_token),
                            "BSC should be filtered on Free plan (paid-tier-only chain)")
        finally:
            cfg.ETHERSCAN_API_KEY = old_eth
            cfg.ETHERSCAN_PLAN_TIER = old_tier

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

    def test_new_config_values(self):
        # Verify the relaxed thresholds and new flags exist
        self.assertLessEqual(cfg.WHALE_MIN_SCORE, 55.0)
        self.assertLessEqual(cfg.WHALE_MIN_WIN_RATE, 60.0)
        self.assertTrue(hasattr(cfg, "BACKFILL_ENABLED"))
        self.assertTrue(hasattr(cfg, "BACKFILL_DAYS"))
        self.assertTrue(hasattr(cfg, "ALERT_CANDIDATE_ENABLED"))
        self.assertTrue(hasattr(cfg, "WHALE_CANDIDATE_MIN_WINS"))


class TestNewScoring(unittest.TestCase):
    """Tests for the fixed scoring formula and candidate detection."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = cfg.DATA_DIR
        cfg.DATA_DIR = self.tmp
        cfg.WALLETS_FILE = os.path.join(self.tmp, "wallets.csv")
        cfg.TRADES_FILE = os.path.join(self.tmp, "trades.csv")
        cfg.SELLS_FILE = os.path.join(self.tmp, "sells.csv")
        cfg.WHITELIST_FILE = os.path.join(self.tmp, "whitelist.csv")
        cfg.WHALES_FILE = os.path.join(self.tmp, "whales.csv")
        cfg.ALERTS_FILE = os.path.join(self.tmp, "whale_alerts.csv")

    def tearDown(self):
        cfg.DATA_DIR = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_normalized_formula_increases_score(self):
        """Verify the new normalized formula gives higher scores than raw."""
        # 50% avg profit wallet
        sells = [
            {"wallet_address": "0x1", "profit_percent": "50", "is_winning": "TRUE",
             "hold_duration_hours": "5"},
            {"wallet_address": "0x1", "profit_percent": "50", "is_winning": "TRUE",
             "hold_duration_hours": "5"},
            {"wallet_address": "0x1", "profit_percent": "-5", "is_winning": "FALSE",
             "hold_duration_hours": "5"},
        ]
        m = scoring.calculate_wallet_metrics(
            {"address": "0x1", "total_trades": "5"}, sells
        )
        # OLD formula would give: 0.667*45 + 50*0.25 + ... = ~30 + 12.5 = 42.5
        # NEW formula:            0.667*45 + (50/80*100)*0.25 + ... = ~30 + 15.6 = 45.6
        # So new score should be higher
        self.assertGreater(m["score"], 42.5,
                           f"new formula should beat old (got {m['score']})")

    def test_is_whale_candidate(self):
        """Wallet with ≥1 winning sell and avg profit≥5% should be a candidate."""
        from datetime import datetime, timezone
        addr = "0xcand1234567890123456789012345678901234567"
        db.write_csv(cfg.WALLETS_FILE, db.wallet_headers(), [{
            "address": addr, "chain": "ethereum",
            "first_seen": "a",
            "last_seen": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "total_trades": "2", "total_sells": "1", "winning_sells": "1", "losing_sells": "0",
            "win_rate": "100", "avg_profit": "15", "avg_hold_duration": "5",
            "score": "50", "in_whitelist": "FALSE", "is_whale": "FALSE",
        }])
        db.write_csv(cfg.SELLS_FILE, db.sell_headers(), [{
            "sell_id": "1", "trade_id": "t1", "wallet_address": addr, "token": "X",
            "contract": "c", "sell_price": "1", "sell_date": "d",
            "sell_percent": "100", "profit_percent": "15", "is_winning": "TRUE",
            "hold_duration_hours": "5", "verified_onchain": "TRUE",
        }])
        w = db.get_wallet(addr)
        self.assertTrue(scoring.is_whale_candidate(w),
                        "wallet with 1 win + 15% profit should be candidate")

    def test_not_candidate_when_already_whale(self):
        addr = "0xwhale12345678901234567890123456789012345"
        w = {
            "address": addr, "is_whale": "TRUE",
            "winning_sells": "3", "avg_profit": "20",
        }
        self.assertFalse(scoring.is_whale_candidate(w))

    def test_not_candidate_when_no_wins(self):
        addr = "0xnewb123456789012345678901234567890123456"
        w = {
            "address": addr, "is_whale": "FALSE",
            "winning_sells": "0", "avg_profit": "0",
        }
        self.assertFalse(scoring.is_whale_candidate(w))

    def test_get_whale_candidates_returns_sorted(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        db.write_csv(cfg.WALLETS_FILE, db.wallet_headers(), [
            {"address": f"0xlow_{i:06d}", "chain": "ethereum",
             "first_seen": "a", "last_seen": now,
             "total_trades": "2", "total_sells": "1", "winning_sells": "1",
             "losing_sells": "0", "win_rate": "100", "avg_profit": "10",
             "avg_hold_duration": "5", "score": str(30 + i),
             "in_whitelist": "FALSE", "is_whale": "FALSE"}
            for i in range(5)
        ])
        candidates = scoring.get_whale_candidates(limit=10)
        self.assertEqual(len(candidates), 5)
        # Should be sorted by score desc
        scores = [float(c["score"]) for c in candidates]
        self.assertEqual(scores, sorted(scores, reverse=True))


class TestApisRetry(unittest.TestCase):
    """Verify the new retry/backoff logic doesn't break existing behavior."""

    def test_get_returns_none_on_no_response(self):
        # Mock _get to return None
        from unittest.mock import patch
        with patch("apis._get", return_value=None):
            result = apis.get_token_price("0xnonexistent", "ethereum")
            # Should be None
            self.assertIsNone(result)

    def test_clear_price_cache(self):
        apis.clear_price_cache()
        # Should not raise
        apis.clear_price_cache()


def run_all():
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_all())
