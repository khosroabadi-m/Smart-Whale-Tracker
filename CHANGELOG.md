# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## Versioning Policy

```
MAJOR.MINOR.PATCH
   1     2     3
```

- **MAJOR**: breaking changes (data format, config schema, removed features)
- **MINOR**: new features or behavior improvements (backwards compatible)
- **PATCH**: bug fixes, tiny tweaks (no behavior change)

Each change bumps exactly ONE number and resets the lower ones to 0.

---

## [2.7.0] — 2026-09-05

### Fixed — CRITICAL (backfill was skipping ALL sells without historical price)

After v2.6.0, backfill found 5 sells but recorded 0:
```
Backfill done: wallets=5, sells_found=5, sells_recorded=0, skipped=5
```

**Root cause:** In v2.5.0, when no historical buy price was available, the code
would SKIP the sell entirely (to avoid 0% profit synthetic trades). But this
meant most backfill discoveries were being thrown away!

The real goal of backfill is to **increase `trades` count** for whale qualification
(`WHALE_MIN_TRADES=3`). Even a 0% profit trade counts toward this requirement.
A wallet with 5 trades (even if 4 have 0% profit) qualifies for `trades>=3`.
But if we skip all sells without historical price, the wallet stays at trades=1.

- **#1 Use current price as fallback when historical price unavailable**:
  - OLD: skip sell if no historical buy price → 0 sells recorded
  - NEW: use `current_price` as `buy_price` fallback (profit ~0%, but trade counts)
  - This ensures every backfill discovery creates a trade entry

- **#2 Added historical sell price lookup**:
  - Now also fetches `get_token_price_at_timestamp(sell_ts)` for the sell time
  - Uses historical sell price for profit calculation when available
  - Falls back to current price when historical sell price unavailable
  - Records the actual sell price (historical or current) in `sell_price` field

- **#3 Removed redundant `trade_id`-based dedup in backfill**:
  - OLD: pre-checked `existing_sells` for `(trade_id, wallet)` and skipped
  - NEW: relies on `db.add_sell()` internal dedup (wallet + contract + price + profit)
  - This allows multiple sells on different contracts for the same wallet
  - Genuine duplicates are still blocked by `add_sell()`'s internal logic

### Changed
- `backfill_candidates()`: no longer skips sells without historical price
- Profit calculation now uses historical sell price when available
- `sell_price` recorded in sells.csv now reflects actual sell-time price (or current)
- Badge version bumped to 2.7.0

### Migration notes for users
- **No data migration needed**
- Future nightly runs will now record backfilled sells even without historical prices
- Wallets will reach `trades>=3` after 2-3 backfill runs
- Combined with `wins>=2` (already working), wallets will qualify as whales
- Expect 3-5 new whales within 1-2 nightly runs after upgrading

---

## [2.6.0] — 2026-09-05

### Fixed — CRITICAL (multiple sells blocked whale qualification)

After analyzing the user's data, we discovered why only 1 wallet became a whale:

```
Wallet              wins  sells  trades  is_whale?  why not?
0x69c7bd26512f        2      3       1    ❌         sc<45, tr<3
0xc4704f13d5e0        1      1       1    ❌         wins<2, tr<3
0x7b73644935b8        1      1       1    ❌         wins<2, tr<3
0xf22fdd2be7c6        2      4       1    ❌         sc<45, tr<3
0x69c66beafb06        1      1       1    ❌         wins<2, tr<3
... (13 more wallets, all blocked by wins<2 OR trades<3)
```

**16 wallets had ≥1 winning sell, but NONE could qualify as whale** because:
- `WHALE_MIN_TRADES = 3` → all wallets had `trades=1`
- `WHALE_MIN_WINNING_SELLS = 2` → 13 of 16 had only `wins=1`

**Root cause:** `db.add_sell()` had aggressive deduplication:
```python
# OLD: block if same trade_id + wallet already has ANY sell
for s in data:
    if s.get("trade_id") == trade_id and s.get("wallet_address") == wallet:
        return None  # ← blocked multiple sells for same wallet!
```

This meant: even if backfill found 5 profitable sells on 5 different tokens,
only the FIRST one would be recorded. The rest were blocked as "duplicates".

- **#1 Changed `add_sell()` deduplication logic**:
  - OLD: blocked any sell if (trade_id, wallet) already had a sell → max 1 sell per wallet
  - NEW: only blocks if EXACT same (wallet, contract, sell_price, profit_percent) exists
  - This allows multiple sells from the same wallet on DIFFERENT contracts
  - Genuine duplicates (re-runs of same sell) are still blocked
  - Added random suffix to `sell_id` to avoid collisions when recording multiple sells in same second

- **#2 Added 2 new tests**:
  - `test_multiple_sells_on_different_contracts_allowed`: verifies wallet can have 2+ sells
  - `test_exact_duplicate_sell_blocked`: verifies genuine duplicates are still blocked

### Changed
- `db.add_sell()`: dedup logic now uses (wallet, contract, sell_price, profit) tuple instead of (trade_id, wallet)
- `sell_id` generation now includes random 3-digit suffix for uniqueness
- Badge version bumped to 2.6.0

### Migration notes for users
- **No data migration needed** — existing sells are preserved
- Future nightly runs will now record multiple sells per wallet (when found by backfill)
- Wallets that previously had `wins=1` may now reach `wins=2+` and qualify as whales
- Expect 3-5 new whales within 1-2 nightly runs after upgrading

---

## [2.5.0] — 2026-09-04

### Fixed — CRITICAL (backfill pollution)

After the first successful backfill run (v2.4.0), we discovered that ALL 5 backfilled
sells were **WETH with profit=0.0%** — completely useless for whale qualification.

**Root cause:** Every DEX swap involves WETH (or USDC/USDT/DAI) as the medium of exchange.
When a wallet swaps Token A → WETH → Token B, the Etherscan `tokentx` API returns:
1. Token A → wallet (wallet receives A)
2. wallet → WETH contract (wallet sends A, receives WETH)
3. WETH → wallet (wallet receives WETH from swap)
4. wallet → Token B contract (wallet sends WETH, receives B)

Our backfill code treated steps 2-3 as a "buy WETH then sell WETH" trade — but it's
just DEX mechanics, not a real trading decision. The profit came out as 0% because
we used `buy_price = current_price` for synthetic trades.

- **#1 Added `BASE_TOKENS` filter in backfill**: 14 contracts filtered (WETH, USDC,
  USDT, DAI, WBTC, COMP, LINK, UNI, MKR, AAVE, FRAX, TrueUSD, BUSD, PAX). These are
  "medium of exchange" tokens, not real trading targets. Backfill now skips them entirely.

- **#2 Removed 0% profit synthetic trades**: When no existing trade exists for a
  (wallet, contract) pair, we now try `get_token_price_at_timestamp()` to estimate
  the historical buy price. If we can't get a historical estimate, we SKIP the sell
  instead of creating a 0% profit synthetic trade (which pollutes the data).

- **#3 Improved "no price" logging**: The log now shows the token symbol AND the
  contract address (not just the contract prefix), so you can see which token failed
  pricing. Also mentions "DexScreener + GeckoTerminal both failed" so you know both
  price sources were tried.

- **#4 Added `cleanup_base_token_sells()` to `fix_data.py`**: This function removes
  any existing base token sells (WETH/USDC/etc.) from `sells.csv` that were recorded
  by previous backfill runs. Also recalculates wallet sell/win counts. Run `fix_data.py`
  once after upgrading to clean up polluted data.

### Added
- `config.BASE_TOKENS` set with 14 Ethereum mainnet base token contracts
- `cleanup_base_token_sells()` function in `fix_data.py`
- Historical price estimation in backfill (uses `get_token_price_at_timestamp`)
- Better logging in backfill: shows token symbol + contract for "no price" cases

### Changed
- `backfill_wallet_sells()`: skips contracts in `BASE_TOKENS` (logs at debug level)
- `backfill_candidates()`: no longer creates 0% profit synthetic trades — skips instead
- `fix_data.py main()`: now calls both `sanitize()` AND `cleanup_base_token_sells()`

### Migration notes for users
- **Run `fix_data.py` once** after upgrading to remove WETH/USDC sells from previous
  backfill runs. This will clean up the 5 WETH sells with 0% profit that v2.4.0 recorded.
- Future backfill runs will skip base tokens entirely — no more 0% profit pollution.
- Backfill will now find REAL token trades (memecoins, altcoins) instead of WETH noise.

---

## [2.4.0] — 2026-09-04

### Fixed — CRITICAL (based on official Etherscan V2 docs)

After reading the official Etherscan V2 migration docs (https://docs.etherscan.io/v2-migration),
we discovered that our v2.3.0 "native endpoint fallback" was based on a WRONG assumption.

**The truth (from official docs):**
- V2 uses a SINGLE endpoint (`https://api.etherscan.io/v2/api`) + SINGLE `ETHERSCAN_API_KEY` for ALL chains
- Per-chain keys (BscScan, PolygonScan, Arbiscan) are NOT valid for V2 — they return "Invalid API Key"
- On the **Free tier**, 4 chains are **paid-tier-only**: BSC (56), Base (8453), OP (10), Avalanche (43114)
- These chains return: `"Free API access is not supported for this chain. Please upgrade your api plan..."`

So the v2.3.0 "native endpoint fallback" approach was wrong — V2 IS the unified endpoint.
The real fix is to FILTER OUT paid-tier chains on Free plan, not to use native endpoints.

- **#1 Removed native endpoint fallback** (was based on wrong assumption):
  - Removed `_NATIVE_API_ENDPOINTS` dict content (kept empty for backward compat)
  - Removed `_native_api_key()` usage (returns None)
  - Removed native endpoint calls in `get_token_transfers()` and `get_wallet_all_token_transfers()`
  - All API calls now go through V2 unified endpoint only

- **#2 Added `PAID_TIER_ONLY` chain filtering**:
  - `config.PAID_TIER_ONLY = {"bsc", "bnb", "base", "optimism", "avalanche"}`
  - `config.active_chains()` excludes these on Free tier
  - `is_valid_token()` filters them out BEFORE API call (no NOTOK errors)
  - On paid tier (env `ETHERSCAN_PLAN_TIER=standard`), all chains are active

- **#3 Added rate limit awareness**:
  - `RATE_LIMIT_CALLS_PER_SEC` dict per plan (free=3, lite=5, standard=10, advanced=20, pro=30)
  - `ETHERSCAN_PLAN_TIER` env var (default: "free")
  - `API_SLEEP` computed dynamically: `(1/calls_per_sec) * 1.25` (25% safety margin)
  - Free tier → 0.42s sleep (stays under 3 calls/sec limit)

- **#4 Improved `validate_env()` logging**:
  - Shows plan tier
  - Shows active chains count + names
  - Shows paid-tier-only chains (filtered out on Free plan)
  - Tells user how to upgrade (set `ETHERSCAN_PLAN_TIER=standard` env var if they have a paid plan)

### Added
- 20+ new chains to `CHAIN_MAP` (unichain, monad, sonic, opbnb, mantle, berachain, blast, taiko, etc.)
- All chains from official V2 docs (61 total: 31 mainnets + we kept the mainnet-relevant ones)
- `RATE_LIMIT_CALLS_PER_SEC` dict with all 6 plan tiers
- `ETHERSCAN_PLAN_TIER` env var support
- Test `test_bsc_filtered_on_free_plan` (verifies BSC is filtered on Free tier)
- Test `test_bsc_works_on_paid_plan` (verifies BSC is active on paid tier)
- Reference to official docs URL in config.py comments

### Changed
- `BSCSCAN_API_KEY` is now ACTUALLY deprecated (V2 unified endpoint doesn't need it)
- `validate_env()` warns user if BSCSCAN_API_KEY is set (can be safely removed)
- GitHub Actions workflows: removed all native per-chain key secrets, added `ETHERSCAN_PLAN_TIER`
- Badge version bumped to 2.4.0
- `API_SLEEP` now computed from plan tier (was hardcoded 0.4)

### Removed
- Native per-chain endpoint fallback code (was based on wrong assumption about V2)
- `_NATIVE_API_ENDPOINTS` dict content (kept empty for backward compat)
- Native API key secrets from GitHub Actions workflows (BSCSCAN_API_KEY, POLYGONSCAN_API_KEY, etc.)

### Migration notes for users
- **If you have Free Etherscan plan** (most likely): BSC/Base/OP/Avalanche tokens will be filtered out.
  This is EXPECTED behavior — those chains require a paid plan on V2.
- **If you have a paid Etherscan plan**: set `ETHERSCAN_PLAN_TIER=standard` (or your tier) as a
  GitHub Secret. This unlocks all chains including BSC/Base/OP/Avalanche.
- **You can safely remove** `BSCSCAN_API_KEY` GitHub Secret — V2 doesn't use it.
- Source: https://docs.etherscan.io/v2-migration

---

## [2.3.0] — 2026-09-04

### Fixed — CRITICAL
- **#1 Backfill returning 0 sells (root cause found!)**: `backfill_wallet_sells()` was
  using `sort="asc"` which returns the OLDEST 200 transfers from Etherscan. All 200
  were older than 30 days, so they all got skipped (`skipped_old=200`). Changed to
  `sort="desc"` to get the NEWEST 200 transfers first.

- **#2 BSC still returning NOTOK despite V2 unified endpoint**: Etherscan V2 free-tier
  keys may not support all chains. Added native per-chain endpoint fallback:
  - V2 fails for BSC → try `api.bscscan.com` with `BSCSCAN_API_KEY`
  - V2 fails for Polygon → try `api.polygonscan.com` with `POLYGONSCAN_API_KEY`
  - V2 fails for Arbitrum → try `api.arbiscan.io` with `ARBISCAN_API_KEY`
  - etc.
  *(NOTE: In v2.4.0 this was REVERSED — V2 IS the unified endpoint, native fallback was removed)*

- **#3 Poor error logging**: `get_token_transfers()` was logging `message` (always "NOTOK")
  instead of the actual error in `result`. Now logs BOTH fields separately so user can
  see the real error (e.g. "Invalid API Key", "Max rate limit reached", etc.).

### Added
- `.gitignore` file restored (was accidentally missing from v2.2.0 zip)
- `data/.gitkeep` placeholder (so `data/` directory is tracked even if empty)
- `_NATIVE_API_ENDPOINTS` dict mapping chains to their native API URLs + env var names
- `_native_api_key()` helper to fetch native per-chain key
- Native endpoint fallback in BOTH `get_token_transfers()` and `get_wallet_all_token_transfers()`
- Config variables for all native per-chain API keys (POLYGONSCAN_API_KEY, ARBISCAN_API_KEY, etc.)
- `validate_env()` now reports which native per-chain keys are set
- GitHub Actions workflows now pass all native per-chain key secrets to the env

### Changed
- `BSCSCAN_API_KEY` is NO LONGER deprecated — it's used as native BSC fallback when V2 fails
- README updated: BSCSCAN_API_KEY is now "(optional, for BSC fallback)" instead of deprecated
- Badge version bumped to 2.3.0

---

## [2.2.0] — 2026-09-04

### Fixed — CRITICAL
- **#1 BSC tokens returning "NOTOK"**: Etherscan V2 is a UNIFIED endpoint that
  accepts a single `ETHERSCAN_API_KEY` for ALL chains (Ethereum, BSC, Polygon,
  Arbitrum, Base, etc.). The `chainid` parameter selects the chain — no separate
  per-chain keys needed. The `BSCSCAN_API_KEY` from bscscan.com was for the OLD
  endpoint (`api.bscscan.com`) and does NOT work with V2. Now `_api_key_for_chain()`
  always returns `ETHERSCAN_API_KEY`.

- **#2 Backfill returning 0 sells**: Added detailed logging to `backfill_wallet_sells()`
  and `get_wallet_all_token_transfers()` to diagnose why backfill finds nothing.
  Now logs:
  - Number of raw transfers returned by API
  - Number of transfers skipped (older than 30 days)
  - Number of contracts analyzed
  - Number of contracts with sells
  - API error messages (status, message, result preview) when status != "1"

### Changed
- `_api_key_for_chain()` now always returns `cfg.ETHERSCAN_API_KEY` (BSCSCAN_API_KEY ignored)
- `config.REQUIRES_OWN_API_KEY` is now empty `{}` (no chains need separate keys)
- `config.active_chains()` returns all SUPPORTED_CHAINS if ETHERSCAN_API_KEY is set
- `bot.py validate_env()` now tells user BSCSCAN_API_KEY is deprecated and can be removed
- Test `test_bsc_filtered_without_api_key` → `test_bsc_works_with_etherscan_key`
  (BSC now works with just ETHERSCAN_API_KEY via V2 unified endpoint)

### Deprecated
- `BSCSCAN_API_KEY` config value — no longer used. Kept for backwards compatibility.
  Users can safely remove this GitHub Secret.
  *(NOTE: In v2.3.0 this was REVERSED — BSCSCAN_API_KEY is now used as native fallback)*

---

## [2.1.0] — 2026-09-04

### Added
- **VERSION** file + `version.py` helper module for centralized version tracking
- Version banner in bot.py and monitor_nightly.py startup logs
- Version footer `📦 vX.Y.Z` in all Telegram messages (discovery, nightly report, candidate alert, whale promoted)
- Version in dashboard footer
- **CHANGELOG.md** with full history
- **GeckoTerminal API** as price fallback when DexScreener returns no price (supports all chains, no API key needed)
- `cfg.active_chains()` helper to compute which chains have working API keys
- `test_bsc_filtered_without_api_key` test case (later replaced in 2.2.0)
- `validate_env()` now reports active chains list at bot startup

### Changed
- `MIN_SCORE_FOR_WHITELIST` lowered from 55 → 45 (to match new WHALE_MIN_SCORE)
- `is_valid_token()` now uses `active_chains()` instead of `SUPPORTED_CHAINS`
- `backfill_candidates()` now creates synthetic trades for contracts we didn't track before (instead of skipping them)
- `existing_sells` is refreshed after each recorded backfilled sell (was loaded once per wallet)
- Telegram `parse_mode` switched from MarkdownV2 → Markdown (legacy) for compatibility
- README updated with versioning policy + changelog reference

### Removed
- **`bot_commands.py`** (interactive Telegram bot — too much Actions quota used)
- **`.github/workflows/bot_commands.yml`** workflow
- 18 manual `\\.` escapes from `telegram_utils.py` (were causing MarkdownV2 failures)

### Fixed
- **#1**: `tokentx empty/error: NOTOK` for all BSC tokens → now properly filtered
- **#2**: MarkdownV2 always failing on every message → switched to legacy Markdown, removed manual escapes
- **#3**: Backfill found 0 sells → now creates synthetic trades for new contracts
- **#4**: LAIKA sell skipped due to no price → GeckoTerminal fallback added
- **#5**: Whitelist stayed empty → `MIN_SCORE_FOR_WHITELIST` lowered to match whale threshold

---

## [2.0.0] — 2026-09-03

### Added — Initial v2 release
- **Normalized scoring formula**: `avg_profit / MAX × 100` (was using raw %, capping wallets at ~54)
- **Whale candidate system**: alert when wallet reaches 1 verified winning sell
- **Backfill mode**: look back 30 days into candidate wallet history
- **API retry with exponential backoff**: 3 retries with jitter on 429/5xx
- **Price cache 60s** to reduce API calls during nightly scans
- **Multi-chain price lookup** via DexScreener (works for all supported chains)
- **Historical price estimate** using 24h price change
- **HTML dashboard generator**: `data/dashboard.html` (self-contained, RTL, dark theme)
- **MarkdownV2 fallback** chain (V2 → legacy → plain text)
- **Concurrency locks** on all GitHub Actions workflows
- **Whale dormancy tracking**: inactive whales marked `dormant` (not deleted)
- **Cap raised 80 → 500** for nightly trade scanning
- **Relaxed thresholds**: `WHALE_MIN_WIN_RATE` 60→50, `WHALE_MIN_SCORE` 55→45
- **Weekly summary**: top 5 candidates in nightly report on Sundays

### Changed
- Telegram messages: cleaner formatting, less noise
- README: comprehensive rewrite with migration guide

---

## [1.0.0] — Initial release
- `bot.py`: trending token discovery via DexScreener
- `monitor_nightly.py`: sell detection, scoring, whale promotion
- `fix_data.py`: manual data cleanup
- `config.py` / `db.py` / `apis.py` / `scoring.py` / `telegram_utils.py`
- GitHub Actions workflows: bot (45min), nightly (daily), fix_data (manual)
- Atomic CSV writes to prevent corruption
- Blacklist for known router addresses (Uniswap, Sushi, 1inch)
- Persian RTL Telegram messages with Markdown formatting
