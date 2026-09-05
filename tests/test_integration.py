#!/usr/bin/env python3
"""
Comprehensive integration test for Smart Whale Tracker v2.8.0+.
Simulates the full nightly flow with mocked API responses to catch bugs
BEFORE deployment. This is the test that should have been written earlier.

Run: python tests/test_integration.py
"""
import os
import sys
import tempfile
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config as cfg
import db
import scoring
import apis
import monitor_nightly
import telegram_utils as tg


def make_mock_tx(from_addr, to_addr, value, ts, contract="0xtoken1234567890123456789012345678901234567", symbol="TEST", decimals=18, hash="0xhash"):
    """Create a mock Etherscan token transfer response."""
    return {
        "timeStamp": str(ts),
        "from": from_addr.lower(),
        "to": to_addr.lower(),
        "value": str(int(value * (10 ** decimals))),
        "tokenDecimal": str(decimals),
        "hash": hash,
        "contractAddress": contract.lower(),
        "tokenSymbol": symbol,
        "tokenName": symbol,
    }


class TestBackfillWalletBuys(unittest.TestCase):
    """Test the new backfill_wallet_buys() function."""

    def test_finds_buy_contracts(self):
        """Should find contracts where wallet received tokens (bought)."""
        wallet = "0xwalletprefix1"
        token_a = "0xtokena1234567890123456789012345678901234567"
        token_b = "0xtokenb1234567890123456789012345678901234567"

        # Wallet bought token A (received) but never sold
        # Wallet bought token B and sold it
        now_ts = int(datetime.now(timezone.utc).timestamp())
        mock_txs = [
            make_mock_tx("0xrouter", wallet, 100, now_ts - 3600, contract=token_a, symbol="TOKA"),
            make_mock_tx(wallet, "0xrouter", 50, now_ts - 1800, contract=token_b, symbol="TOKB"),
            make_mock_tx("0xrouter", wallet, 100, now_ts - 3600, contract=token_b, symbol="TOKB"),
        ]

        with patch("apis.get_wallet_all_token_transfers", return_value=mock_txs):
            result = apis.backfill_wallet_buys(wallet, "ethereum", days_back=30, max_tokens=25)

        contracts_found = {r["contract"] for r in result}
        self.assertIn(token_a, contracts_found, "Should find token A (bought, not sold)")
        self.assertIn(token_b, contracts_found, "Should find token B (bought and sold)")

    def test_skips_base_tokens(self):
        """Should skip WETH, USDC, etc."""
        wallet = "0xwallet2prefix"
        weth = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"  # WETH
        real_token = "0xreal1234567890123456789012345678901234567"

        now_ts = int(datetime.now(timezone.utc).timestamp())
        mock_txs = [
            make_mock_tx("0xrouter", wallet, 100, now_ts - 3600, contract=weth, symbol="WETH"),
            make_mock_tx("0xrouter", wallet, 100, now_ts - 3600, contract=real_token, symbol="REAL"),
        ]

        with patch("apis.get_wallet_all_token_transfers", return_value=mock_txs):
            result = apis.backfill_wallet_buys(wallet, "ethereum", days_back=30, max_tokens=25)

        contracts_found = {r["contract"] for r in result}
        self.assertNotIn(weth, contracts_found, "WETH should be filtered out")
        self.assertIn(real_token, contracts_found, "Real token should be found")

    def test_handles_empty_api_response(self):
        """Should handle case where API returns no transfers."""
        with patch("apis.get_wallet_all_token_transfers", return_value=[]):
            result = apis.backfill_wallet_buys("0xwallet3", "ethereum")
        self.assertEqual(result, [])

    def test_handles_none_api_response(self):
        """Should handle case where API returns None."""
        with patch("apis.get_wallet_all_token_transfers", return_value=None):
            result = apis.backfill_wallet_buys("0xwallet4", "ethereum")
        self.assertEqual(result, [])

    def test_respects_max_tokens_limit(self):
        """Should limit results to max_tokens."""
        wallet = "0xwallet5prefix"
        now_ts = int(datetime.now(timezone.utc).timestamp())
        mock_txs = []
        for i in range(10):
            contract = f"0x{i:064d}"[-40:]
            mock_txs.append(make_mock_tx("0xrouter", wallet, 100, now_ts - 3600, contract=contract, symbol=f"T{i}"))

        with patch("apis.get_wallet_all_token_transfers", return_value=mock_txs):
            result = apis.backfill_wallet_buys(wallet, "ethereum", days_back=30, max_tokens=3)
        self.assertLessEqual(len(result), 3)

    def test_filters_old_transfers(self):
        """Should skip transfers older than days_back."""
        wallet = "0xwallet6prefix"
        old_ts = int((datetime.now(timezone.utc) - timedelta(days=60)).timestamp())
        recent_ts = int(datetime.now(timezone.utc).timestamp())
        old_contract = "0xold0000000000000000000000000000000000000001"
        new_contract = "0xnew0000000000000000000000000000000000000002"

        mock_txs = [
            make_mock_tx("0xrouter", wallet, 100, old_ts, contract=old_contract, symbol="OLD"),
            make_mock_tx("0xrouter", wallet, 100, recent_ts, contract=new_contract, symbol="NEW"),
        ]

        with patch("apis.get_wallet_all_token_transfers", return_value=mock_txs):
            result = apis.backfill_wallet_buys(wallet, "ethereum", days_back=30, max_tokens=25)

        contracts_found = {r["contract"] for r in result}
        self.assertNotIn(old_contract, contracts_found, "Old transfer should be filtered")
        self.assertIn(new_contract, contracts_found, "Recent transfer should be found")


class TestBackfillCandidatesFullFlow(unittest.TestCase):
    """Test the full backfill_candidates() flow with mocked APIs."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_dir = cfg.DATA_DIR
        cfg.DATA_DIR = self.tmp
        cfg.WALLETS_FILE = os.path.join(self.tmp, "wallets.csv")
        cfg.TRADES_FILE = os.path.join(self.tmp, "trades.csv")
        cfg.SELLS_FILE = os.path.join(self.tmp, "sells.csv")
        cfg.WHITELIST_FILE = os.path.join(self.tmp, "whitelist.csv")
        cfg.WHALES_FILE = os.path.join(self.tmp, "whales.csv")
        cfg.ALERTS_FILE = os.path.join(self.tmp, "whale_alerts.csv")
        cfg.NIGHTLY_LOG_FILE = os.path.join(self.tmp, "nightly_log.csv")
        cfg.LOGS_DIR = os.path.join(self.tmp, "logs")
        os.makedirs(cfg.LOGS_DIR, exist_ok=True)

    def tearDown(self):
        cfg.DATA_DIR = self._old_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _create_candidate_wallet(self, addr, wins=1, sells=1, trades=1, score=50.0, avg_profit=10.0):
        """Helper: create a wallet with given metrics that qualifies as candidate."""
        now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        db.write_csv(cfg.WALLETS_FILE, db.wallet_headers(), [{
            "address": addr, "chain": "ethereum",
            "first_seen": now_iso, "last_seen": now_iso,
            "total_trades": str(trades), "total_sells": str(sells),
            "winning_sells": str(wins), "losing_sells": str(sells - wins),
            "win_rate": str(round(wins * 100.0 / max(sells, 1), 2)),
            "avg_profit": str(avg_profit), "avg_hold_duration": "5",
            "score": str(score), "in_whitelist": "FALSE", "is_whale": "FALSE",
        }])

    def test_creates_new_trades_from_buys(self):
        """backfill_candidates() should create new trades when wallet bought tokens we don't track."""
        addr = "0xtestwallet1abcd"
        self._create_candidate_wallet(addr, wins=1, sells=1, trades=1)

        # Wallet bought 3 different tokens, but we only track 1
        tracked_contract = "0xtracked1234567890123456789012345678901234"
        new_contract_1 = "0xnew1111111111111111111111111111111111111111"
        new_contract_2 = "0xnew2222222222222222222222222222222222222222"

        now_ts = int(datetime.now(timezone.utc).timestamp())
        mock_buy_txs = [
            make_mock_tx("0xrouter", addr, 100, now_ts - 3600, contract=tracked_contract, symbol="TRK"),
            make_mock_tx("0xrouter", addr, 100, now_ts - 3600, contract=new_contract_1, symbol="NEW1"),
            make_mock_tx("0xrouter", addr, 100, now_ts - 3600, contract=new_contract_2, symbol="NEW2"),
        ]

        with patch("apis.backfill_wallet_buys", return_value=[
            {"contract": tracked_contract, "token_symbol": "TRK", "buy_timestamp": now_ts - 3600, "buy_hash": "0xh1", "amount_in": 100, "amount_out": 0, "has_sell": False},
            {"contract": new_contract_1, "token_symbol": "NEW1", "buy_timestamp": now_ts - 3600, "buy_hash": "0xh2", "amount_in": 100, "amount_out": 0, "has_sell": False},
            {"contract": new_contract_2, "token_symbol": "NEW2", "buy_timestamp": now_ts - 3600, "buy_hash": "0xh3", "amount_in": 100, "amount_out": 0, "has_sell": False},
        ]), patch("apis.backfill_wallet_sells", return_value=[]), \
             patch("apis.get_token_price", return_value=0.001), \
             patch("apis.get_token_price_at_timestamp", return_value=0.001):

            stats = monitor_nightly.backfill_candidates()

        # Should have created 2 new trades (for new_contract_1 and new_contract_2)
        trades = db.read_csv(cfg.TRADES_FILE, db.trade_headers())
        self.assertGreaterEqual(len(trades), 2, f"Should have 2+ trades, got {len(trades)}")

        # Wallet's total_trades should have increased
        w = db.get_wallet(addr)
        self.assertGreaterEqual(int(w["total_trades"]), 3, f"trades should be 3+, got {w['total_trades']}")

    def test_does_not_create_duplicate_trades(self):
        """Should not create a trade for a contract we already track."""
        addr = "0xtestwallet2abcd"
        self._create_candidate_wallet(addr, wins=1, sells=1, trades=1)

        # Add an existing trade
        existing_contract = "0xexisting1234567890123456789012345678901234"
        db.add_trade(addr, {"symbol": "EX", "name": "Existing", "contract": existing_contract}, 0.001, "ethereum")

        # Backfill reports the same contract as a buy
        now_ts = int(datetime.now(timezone.utc).timestamp())
        with patch("apis.backfill_wallet_buys", return_value=[
            {"contract": existing_contract, "token_symbol": "EX", "buy_timestamp": now_ts - 3600, "buy_hash": "0xh1", "amount_in": 100, "amount_out": 0, "has_sell": False},
        ]), patch("apis.backfill_wallet_sells", return_value=[]), \
             patch("apis.get_token_price", return_value=0.001), \
             patch("apis.get_token_price_at_timestamp", return_value=0.001):

            stats = monitor_nightly.backfill_candidates()

        # Should NOT have created a duplicate trade
        trades = db.read_csv(cfg.TRADES_FILE, db.trade_headers())
        matching = [t for t in trades if t.get("contract") == existing_contract]
        self.assertEqual(len(matching), 1, "Should not create duplicate trade for existing contract")

    def test_handles_api_error_gracefully(self):
        """Should handle API errors without crashing."""
        addr = "0xtestwallet3abcd"
        self._create_candidate_wallet(addr)

        with patch("apis.backfill_wallet_buys", side_effect=Exception("API error")), \
             patch("apis.backfill_wallet_sells", side_effect=Exception("API error")):
            # Should not raise
            stats = monitor_nightly.backfill_candidates()

        self.assertEqual(stats["wallets_backfilled"], 0)

    def test_handles_no_price_for_token(self):
        """Should skip trade creation when no price available (DexScreener + GeckoTerminal both fail)."""
        addr = "0xtestwallet4abcd"
        self._create_candidate_wallet(addr)

        new_contract = "0xnoprice1234567890123456789012345678901234567"
        now_ts = int(datetime.now(timezone.utc).timestamp())

        with patch("apis.backfill_wallet_buys", return_value=[
            {"contract": new_contract, "token_symbol": "NOPRICE", "buy_timestamp": now_ts - 3600, "buy_hash": "0xh1", "amount_in": 100, "amount_out": 0, "has_sell": False},
        ]), patch("apis.backfill_wallet_sells", return_value=[]), \
             patch("apis.get_token_price", return_value=None), \
             patch("apis.get_token_price_at_timestamp", return_value=None):

            stats = monitor_nightly.backfill_candidates()

        # Should not have created a trade (no price available)
        trades = db.read_csv(cfg.TRADES_FILE, db.trade_headers())
        matching = [t for t in trades if t.get("contract") == new_contract]
        self.assertEqual(len(matching), 0, "Should not create trade without price")

    def test_records_sells_with_profit(self):
        """Should record sells with profit calculation."""
        addr = "0xtestwallet5abcd"
        self._create_candidate_wallet(addr)

        contract = "0xsell1234567890123456789012345678901234567abc"
        now_ts = int(datetime.now(timezone.utc).timestamp())
        buy_ts = now_ts - 3600  # 1 hour ago
        sell_ts = now_ts - 1800  # 30 min ago

        # Create an existing trade first
        db.add_trade(addr, {"symbol": "SELL", "name": "Sell Token", "contract": contract}, 0.001, "ethereum")

        with patch("apis.backfill_wallet_buys", return_value=[]), \
             patch("apis.backfill_wallet_sells", return_value=[
                 {"contract": contract, "token_symbol": "SELL",
                  "buy_timestamp": buy_ts, "buy_hash": "0xbuy",
                  "sell_timestamp": sell_ts, "sell_hash": "0xsell",
                  "sold_percent": 100.0, "amount_in": 100, "amount_out": 100},
             ]), patch("apis.get_token_price", return_value=0.002), \
             patch("apis.get_token_price_at_timestamp", return_value=0.001):

            stats = monitor_nightly.backfill_candidates()

        # Should have recorded the sell
        sells = db.read_csv(cfg.SELLS_FILE, db.sell_headers())
        self.assertGreaterEqual(len(sells), 1, "Should have recorded at least 1 sell")
        self.assertEqual(sells[0]["token"], "SELL")


    def test_backfill_with_existing_trade_and_sell(self):
        """Should handle case where trade exists AND sell is found (the UnboundLocalError case)."""
        addr = "0xexistingtrade1"
        self._create_candidate_wallet(addr, wins=1, sells=1, trades=1)

        # Create an existing trade for this wallet
        contract = "0xexisting1234567890123456789012345678901234567"
        db.add_trade(addr, {"symbol": "EX", "name": "Existing", "contract": contract}, 0.001, "ethereum")

        now_ts = int(datetime.now(timezone.utc).timestamp())
        buy_ts = now_ts - 3600
        sell_ts = now_ts - 1800

        # backfill_wallet_sells returns a sell for the SAME contract we already have
        with patch("apis.backfill_wallet_buys", return_value=[]), \
             patch("apis.backfill_wallet_sells", return_value=[
                 {"contract": contract, "token_symbol": "EX",
                  "buy_timestamp": buy_ts, "buy_hash": "0xbuy",
                  "sell_timestamp": sell_ts, "sell_hash": "0xsell",
                  "sold_percent": 100.0, "amount_in": 100, "amount_out": 100},
             ]), patch("apis.get_token_price", return_value=0.002), \
             patch("apis.get_token_price_at_timestamp", return_value=0.001):
            # This should NOT raise UnboundLocalError (the v2.7.0 bug)
            stats = monitor_nightly.backfill_candidates()

        # Should have recorded the sell
        sells = db.read_csv(cfg.SELLS_FILE, db.sell_headers())
        self.assertGreaterEqual(len(sells), 1, "Should have recorded the sell")

    def test_backfill_with_new_contract_and_sell(self):
        """Should handle case where trade does NOT exist AND sell is found."""
        addr = "0xnewcontract1"
        self._create_candidate_wallet(addr, wins=1, sells=1, trades=1)

        contract = "0xnew1234567890123456789012345678901234567abcd"
        now_ts = int(datetime.now(timezone.utc).timestamp())
        buy_ts = now_ts - 3600
        sell_ts = now_ts - 1800

        with patch("apis.backfill_wallet_buys", return_value=[]), \
             patch("apis.backfill_wallet_sells", return_value=[
                 {"contract": contract, "token_symbol": "NEW",
                  "buy_timestamp": buy_ts, "buy_hash": "0xbuy",
                  "sell_timestamp": sell_ts, "sell_hash": "0xsell",
                  "sold_percent": 100.0, "amount_in": 100, "amount_out": 100},
             ]), patch("apis.get_token_price", return_value=0.002), \
             patch("apis.get_token_price_at_timestamp", return_value=0.001):
            # This should NOT raise UnboundLocalError
            stats = monitor_nightly.backfill_candidates()

        # Should have created a trade and recorded a sell
        trades = db.read_csv(cfg.TRADES_FILE, db.trade_headers())
        sells = db.read_csv(cfg.SELLS_FILE, db.sell_headers())
        self.assertGreaterEqual(len(trades), 1, "Should have created a trade")
        self.assertGreaterEqual(len(sells), 1, "Should have recorded a sell")


class TestNightlyMainFlow(unittest.TestCase):
    """Test the main() function doesn't crash with various scenarios."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_dir = cfg.DATA_DIR
        cfg.DATA_DIR = self.tmp
        cfg.WALLETS_FILE = os.path.join(self.tmp, "wallets.csv")
        cfg.TRADES_FILE = os.path.join(self.tmp, "trades.csv")
        cfg.SELLS_FILE = os.path.join(self.tmp, "sells.csv")
        cfg.WHITELIST_FILE = os.path.join(self.tmp, "whitelist.csv")
        cfg.WHALES_FILE = os.path.join(self.tmp, "whales.csv")
        cfg.ALERTS_FILE = os.path.join(self.tmp, "whale_alerts.csv")
        cfg.NIGHTLY_LOG_FILE = os.path.join(self.tmp, "nightly_log.csv")
        cfg.LOGS_DIR = os.path.join(self.tmp, "logs")
        os.makedirs(cfg.LOGS_DIR, exist_ok=True)
        # Disable Telegram for tests
        self._old_tg = cfg.TELEGRAM_TOKEN
        cfg.TELEGRAM_TOKEN = ""

    def tearDown(self):
        cfg.DATA_DIR = self._old_dir
        cfg.TELEGRAM_TOKEN = self._old_tg
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_main_with_empty_data(self):
        """main() should not crash with empty data files."""
        # Run main with no data
        with patch("apis.get_token_transfers", return_value=[]), \
             patch("apis.get_wallet_token_transfers", return_value=[]), \
             patch("apis.get_token_price", return_value=None), \
             patch("apis.get_token_price_at_timestamp", return_value=None), \
             patch("apis.backfill_wallet_buys", return_value=[]), \
             patch("apis.backfill_wallet_sells", return_value=[]):
            exit_code = monitor_nightly.main()
        self.assertEqual(exit_code, 0)

    def test_main_with_data_but_no_candidates(self):
        """main() should not crash when there are wallets but no candidates."""
        # Create a wallet with 0 wins (not a candidate)
        addr = "0xnowins1234567890123456789012345678901234567"
        now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        db.write_csv(cfg.WALLETS_FILE, db.wallet_headers(), [{
            "address": addr, "chain": "ethereum",
            "first_seen": now_iso, "last_seen": now_iso,
            "total_trades": "1", "total_sells": "0",
            "winning_sells": "0", "losing_sells": "0",
            "win_rate": "0", "avg_profit": "0", "avg_hold_duration": "0",
            "score": "0", "in_whitelist": "FALSE", "is_whale": "FALSE",
        }])

        with patch("apis.get_token_transfers", return_value=[]), \
             patch("apis.get_wallet_token_transfers", return_value=[]), \
             patch("apis.get_token_price", return_value=None), \
             patch("apis.get_token_price_at_timestamp", return_value=None), \
             patch("apis.backfill_wallet_buys", return_value=[]), \
             patch("apis.backfill_wallet_sells", return_value=[]):
            exit_code = monitor_nightly.main()
        self.assertEqual(exit_code, 0)

    def test_main_with_candidate_and_mock_backfill(self):
        """main() should not crash with a candidate wallet and mocked backfill.
        This test verifies main() completes without error. Trade creation
        is verified separately in test_creates_new_trades_from_buys."""
        addr = "0xcandidate1234567890123456789012345678901234"
        now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        now_ts = int(datetime.now(timezone.utc).timestamp())

        db.write_csv(cfg.WALLETS_FILE, db.wallet_headers(), [{
            "address": addr, "chain": "ethereum",
            "first_seen": now_iso, "last_seen": now_iso,
            "total_trades": "1", "total_sells": "1",
            "winning_sells": "1", "losing_sells": "0",
            "win_rate": "100", "avg_profit": "15", "avg_hold_duration": "5",
            "score": "50", "in_whitelist": "FALSE", "is_whale": "FALSE",
        }])

        # Mock: backfill finds 2 new contracts the wallet bought
        new_contract_1 = "0xnew1111111111111111111111111111111111111111"
        new_contract_2 = "0xnew2222222222222222222222222222222222222222"

        with patch("apis.get_token_transfers", return_value=[]), \
             patch("apis.get_wallet_token_transfers", return_value=[]), \
             patch("apis.get_wallet_all_token_transfers", return_value=[]), \
             patch("apis.get_token_price", return_value=0.001), \
             patch("apis.get_token_price_at_timestamp", return_value=0.001), \
             patch("apis.backfill_wallet_buys", return_value=[
                 {"contract": new_contract_1, "token_symbol": "NEW1", "buy_timestamp": now_ts - 3600, "buy_hash": "0xh1", "amount_in": 100, "amount_out": 0, "has_sell": False},
                 {"contract": new_contract_2, "token_symbol": "NEW2", "buy_timestamp": now_ts - 3600, "buy_hash": "0xh2", "amount_in": 100, "amount_out": 0, "has_sell": False},
             ]), patch("apis.backfill_wallet_sells", return_value=[]):
            exit_code = monitor_nightly.main()
        self.assertEqual(exit_code, 0, "main() should exit 0 (success)")


class TestEdgeCases(unittest.TestCase):
    """Test edge cases that could cause crashes."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_dir = cfg.DATA_DIR
        cfg.DATA_DIR = self.tmp
        cfg.WALLETS_FILE = os.path.join(self.tmp, "wallets.csv")
        cfg.TRADES_FILE = os.path.join(self.tmp, "trades.csv")
        cfg.SELLS_FILE = os.path.join(self.tmp, "sells.csv")
        cfg.WHITELIST_FILE = os.path.join(self.tmp, "whitelist.csv")
        cfg.WHALES_FILE = os.path.join(self.tmp, "whales.csv")
        cfg.ALERTS_FILE = os.path.join(self.tmp, "whale_alerts.csv")

    def tearDown(self):
        cfg.DATA_DIR = self._old_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_sell_with_zero_price(self):
        """add_sell should handle zero sell_price without crash."""
        addr = "0xzero1234567890123456789012345678901234567"
        tid = db.add_trade(addr, {"symbol": "Z", "name": "Z", "contract": "0xzero01234567890123456789012345678901234"}, 0.001, "ethereum")
        sid = db.add_sell(tid, addr, "Z", "0xzero01234567890123456789012345678901234",
                         sell_price=0, sell_percent=100, profit_percent=0,
                         is_winning=False, hold_duration=5.0, verified_onchain=True)
        # Should not crash, sid might be None if 0 price is treated as invalid
        # but the function should not raise an exception

    def test_add_trade_with_empty_contract(self):
        """add_trade should return None for empty contract."""
        sid = db.add_trade("0xtest1234567890123456789012345678901234567",
                          {"symbol": "X", "name": "X", "contract": ""}, 0.001, "ethereum")
        self.assertIsNone(sid)

    def test_promote_whales_with_empty_wallets(self):
        """promote_whales should not crash with no wallets."""
        result = scoring.promote_whales()
        self.assertEqual(len(result), 0)

    def test_calculate_metrics_with_empty_sells(self):
        """calculate_wallet_metrics should handle empty sells list."""
        m = scoring.calculate_wallet_metrics(
            {"address": "0x1", "total_trades": "5"}, []
        )
        self.assertEqual(m["score"], 0.0)


def run_all():
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_all())
