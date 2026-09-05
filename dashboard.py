#!/usr/bin/env python3
"""
Generate a static HTML dashboard from local CSV data.
Run after nightly job — outputs data/dashboard.html.

Sections:
  - KPI cards (wallets, trades, sells, whales, candidates)
  - Top 20 wallets table (sortable)
  - Whale list table
  - Candidate list table
  - Recent sells table
  - Nightly activity chart (last 30 runs)

Self-contained: no external JS/CSS — all inline.
"""
import os
import sys
import html
from datetime import datetime, timezone
from typing import List, Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as cfg
import db
import scoring
import version


def _esc(s) -> str:
    return html.escape(str(s or ""))


def _short(addr: str) -> str:
    if not addr or len(addr) < 14:
        return addr or "?"
    return f"{addr[:8]}…{addr[-6:]}"


def _chain_link(addr: str, chain: str) -> str:
    chain = (chain or "ethereum").lower()
    base = {
        "ethereum": "https://etherscan.io/address/",
        "eth": "https://etherscan.io/address/",
        "bsc": "https://bscscan.com/address/",
        "bnb": "https://bscscan.com/address/",
        "polygon": "https://polygonscan.com/address/",
        "arbitrum": "https://arbiscan.io/address/",
        "base": "https://basescan.org/address/",
        "optimism": "https://optimistic.etherscan.io/address/",
    }.get(chain, "https://etherscan.io/address/")
    return f"{base}{addr}"


def render_kpi(label: str, value, accent: str = "#3b82f6") -> str:
    return f"""
    <div class="kpi-card" style="border-top: 4px solid {accent};">
      <div class="kpi-label">{_esc(label)}</div>
      <div class="kpi-value">{_esc(value)}</div>
    </div>
    """


def render_wallet_table(wallets: List[Dict], limit: int = 20) -> str:
    rows = []
    for w in wallets[:limit]:
        addr = w.get("address", "")
        chain = w.get("chain", "ethereum")
        is_whale = (w.get("is_whale") or "").upper() == "TRUE"
        is_wl = (w.get("in_whitelist") or "").upper() == "TRUE"
        badge = ""
        if is_whale:
            badge = '<span class="badge whale">🐋 Whale</span>'
        elif is_wl:
            badge = '<span class="badge wl">⭐ WL</span>'

        score = float(w.get("score") or 0)
        score_class = "score-high" if score >= 55 else "score-mid" if score >= 45 else "score-low"

        rows.append(f"""
        <tr>
          <td><a href="{_chain_link(addr, chain)}" target="_blank">{_esc(_short(addr))}</a> {badge}</td>
          <td>{_esc(chain)}</td>
          <td class="{score_class}">{score:.2f}</td>
          <td>{_esc(w.get('win_rate', '0'))}%</td>
          <td>{_esc(w.get('winning_sells', '0'))}/{_esc(w.get('total_sells', '0'))}</td>
          <td>{_esc(w.get('total_trades', '0'))}</td>
          <td>{_esc(w.get('avg_profit', '0'))}%</td>
        </tr>
        """)
    return f"""
    <table class="data-table">
      <thead>
        <tr>
          <th>Wallet</th><th>Chain</th><th>Score</th><th>WinRate</th>
          <th>Wins/Sells</th><th>Trades</th><th>Avg Profit</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows) or '<tr><td colspan="7" class="empty">No wallets yet</td></tr>'}
      </tbody>
    </table>
    """


def render_whales_table(whales: List[Dict]) -> str:
    if not whales:
        return '<div class="empty-state"> هنوز هیچ نهنگی شناسایی نشده 😴</div>'
    rows = []
    for w in whales:
        addr = w.get("address", "")
        chain = w.get("chain", "ethereum")
        promoted = (w.get("promoted_at") or "")[:10]
        rows.append(f"""
        <tr>
          <td><a href="{_chain_link(addr, chain)}" target="_blank">{_esc(_short(addr))}</a></td>
          <td>{_esc(chain)}</td>
          <td>{_esc(w.get('score', '0'))}</td>
          <td>{_esc(w.get('win_rate', '0'))}%</td>
          <td>{_esc(w.get('winning_sells', '0'))}/{_esc(w.get('total_trades', '0'))}</td>
          <td>{_esc(promoted)}</td>
          <td>{_esc(w.get('status', 'active'))}</td>
        </tr>
        """)
    return f"""
    <table class="data-table">
      <thead><tr>
        <th>Address</th><th>Chain</th><th>Score</th><th>WinRate</th>
        <th>Wins/Trades</th><th>Promoted</th><th>Status</th>
      </tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """


def render_candidates_table(candidates: List[Dict]) -> str:
    if not candidates:
        return '<div class="empty-state">هنوز کاندیدایی نیست 🥚</div>'
    rows = []
    for c in candidates:
        addr = c.get("address", "")
        chain = c.get("chain", "ethereum")
        score = float(c.get("score") or 0)
        to_whale = max(0, 45.0 - score)
        rows.append(f"""
        <tr>
          <td><a href="{_chain_link(addr, chain)}" target="_blank">{_esc(_short(addr))}</a></td>
          <td>{_esc(chain)}</td>
          <td>{score:.2f}</td>
          <td>{_esc(c.get('win_rate', '0'))}%</td>
          <td>{_esc(c.get('winning_sells', '0'))}</td>
          <td>{_esc(c.get('avg_profit', '0'))}%</td>
          <td>+{to_whale:.1f}</td>
        </tr>
        """)
    return f"""
    <table class="data-table">
      <thead><tr>
        <th>Address</th><th>Chain</th><th>Score</th><th>WinRate</th>
        <th>Wins</th><th>Avg Profit</th><th>To Whale</th>
      </tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """


def render_recent_sells(sells: List[Dict], limit: int = 20) -> str:
    if not sells:
        return '<div class="empty-state">هنوز فروشی ثبت نشده</div>'
    recent = list(reversed(sells[-limit:]))
    rows = []
    for s in recent:
        profit = float(s.get("profit_percent") or 0)
        cls = "profit-pos" if profit > 0 else "profit-neg"
        is_win = (s.get("is_winning") or "").upper() == "TRUE"
        win_badge = '<span class="badge win">W</span>' if is_win else '<span class="badge loss">L</span>'
        rows.append(f"""
        <tr>
          <td>{win_badge} {_esc(s.get('token', '?'))}</td>
          <td><a href="{_chain_link(s.get('wallet_address',''), s.get('chain','ethereum'))}" target="_blank">{_esc(_short(s.get('wallet_address','')))}</a></td>
          <td class="{cls}">{profit:+.1f}%</td>
          <td>{_esc(s.get('sell_percent', '0'))}%</td>
          <td>{float(s.get("hold_duration_hours") or 0):.1f}h</td>
          <td>{_esc((s.get('sell_date') or '')[:16])}</td>
        </tr>
        """)
    return f"""
    <table class="data-table">
      <thead><tr>
        <th>Token</th><th>Wallet</th><th>Profit</th>
        <th>Sold%</th><th>Hold</th><th>Date</th>
      </tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    """


def render_nightly_chart(nightly_log: List[Dict]) -> str:
    if not nightly_log:
        return '<div class="empty-state">هنوز جاب شبانه‌ای اجرا نشده</div>'
    # build bars of sell_recorded per run
    runs = nightly_log[-30:]
    max_sells = max((int(r.get("sell_recorded") or 0) for r in runs), default=1) or 1
    bars = []
    for r in runs:
        s = int(r.get("sell_recorded") or 0)
        height_pct = (s / max_sells) * 100 if max_sells else 0
        label = (r.get("run_id") or "")[4:8] + "-" + (r.get("run_id") or "")[8:10]  # YYYYMMDD_HHMMSS
        bars.append(f"""
        <div class="bar-wrap" title="{_esc(r.get('run_id',''))}: {s} sells">
          <div class="bar" style="height: {height_pct:.0f}%"></div>
          <div class="bar-label">{_esc(label)}</div>
        </div>
        """)
    return f"""
    <div class="chart">
      <div class="chart-title">Sells recorded per nightly run (last {len(runs)} runs)</div>
      <div class="bar-chart">{''.join(bars)}</div>
    </div>
    """


def generate_dashboard() -> str:
    db.ensure_data_dir()
    wallets = db.read_csv(cfg.WALLETS_FILE, db.wallet_headers())
    trades = db.read_csv(cfg.TRADES_FILE, db.trade_headers())
    sells = db.read_csv(cfg.SELLS_FILE, db.sell_headers())
    whales = db.get_whales()
    candidates = scoring.get_whale_candidates(limit=20)
    nightly_log = db.read_csv(cfg.NIGHTLY_LOG_FILE)
    alerts = db.read_csv(cfg.ALERTS_FILE, db.alert_headers())

    top_wallets = sorted(wallets, key=lambda w: float(w.get("score") or 0), reverse=True)
    active_whales = [w for w in whales if (w.get("status") or "active") == "active"]
    winning_sells = [s for s in sells if (s.get("is_winning") or "").upper() == "TRUE"]
    open_trades = [t for t in trades if t.get("status") in ("open", "partially_sold")]

    now_str = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Smart Whale Tracker — Dashboard</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{
    font-family: 'Vazirmatn', 'Segoe UI', Tahoma, sans-serif;
    background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
    color: #e2e8f0;
    margin: 0; padding: 24px;
    min-height: 100vh;
  }}
  .container {{ max-width: 1280px; margin: 0 auto; }}
  h1 {{
    background: linear-gradient(90deg, #06b6d4, #3b82f6);
    -webkit-background-clip: text; background-clip: text;
    color: transparent;
    font-size: 32px; margin: 0 0 4px 0;
  }}
  .subtitle {{ color: #94a3b8; font-size: 14px; margin-bottom: 24px; }}
  .grid {{ display: grid; gap: 16px; }}
  .kpis {{ grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }}
  .section {{
    background: rgba(30, 41, 59, 0.6);
    backdrop-filter: blur(12px);
    border: 1px solid rgba(148, 163, 184, 0.1);
    border-radius: 12px;
    padding: 20px;
    margin-top: 16px;
  }}
  .section h2 {{ margin: 0 0 16px 0; font-size: 20px; color: #f1f5f9; }}
  .kpi-card {{
    background: rgba(30, 41, 59, 0.8);
    border-radius: 10px;
    padding: 18px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    transition: transform 0.2s;
  }}
  .kpi-card:hover {{ transform: translateY(-2px); }}
  .kpi-label {{ font-size: 12px; color: #94a3b8; margin-bottom: 6px; }}
  .kpi-value {{ font-size: 28px; font-weight: 700; color: #f1f5f9; }}
  table.data-table {{
    width: 100%; border-collapse: collapse;
    font-size: 13px;
  }}
  .data-table th {{
    text-align: right; padding: 10px 8px;
    color: #94a3b8; font-weight: 600;
    border-bottom: 2px solid rgba(148, 163, 184, 0.2);
    font-size: 11px; text-transform: uppercase;
  }}
  .data-table td {{
    padding: 10px 8px;
    border-bottom: 1px solid rgba(148, 163, 184, 0.08);
  }}
  .data-table a {{
    color: #06b6d4; text-decoration: none;
    font-family: 'Fira Code', monospace; font-size: 12px;
  }}
  .data-table a:hover {{ text-decoration: underline; }}
  .badge {{
    display: inline-block; padding: 2px 6px;
    border-radius: 4px; font-size: 10px; font-weight: 600;
    margin-right: 4px;
  }}
  .whale {{ background: #06b6d4; color: #0f172a; }}
  .wl {{ background: #f59e0b; color: #0f172a; }}
  .win {{ background: #10b981; color: #0f172a; }}
  .loss {{ background: #ef4444; color: #f1f5f9; }}
  .score-high {{ color: #10b981; font-weight: 700; }}
  .score-mid {{ color: #f59e0b; font-weight: 600; }}
  .score-low {{ color: #64748b; }}
  .profit-pos {{ color: #10b981; font-weight: 600; }}
  .profit-neg {{ color: #ef4444; font-weight: 600; }}
  .empty-state {{
    padding: 32px; text-align: center;
    color: #64748b; font-style: italic;
  }}
  .chart {{ margin-top: 8px; }}
  .chart-title {{ font-size: 13px; color: #94a3b8; margin-bottom: 12px; }}
  .bar-chart {{
    display: flex; align-items: flex-end;
    gap: 4px; height: 120px;
    padding: 8px; background: rgba(0,0,0,0.2);
    border-radius: 8px;
  }}
  .bar-wrap {{ flex: 1; display: flex; flex-direction: column; align-items: center; min-width: 0; }}
  .bar {{
    width: 100%; background: linear-gradient(180deg, #06b6d4, #3b82f6);
    border-radius: 3px 3px 0 0;
    min-height: 2px;
  }}
  .bar-label {{
    font-size: 9px; color: #64748b;
    margin-top: 4px; transform: rotate(0);
    overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; width: 100%; text-align: center;
  }}
  footer {{ text-align: center; color: #64748b; font-size: 12px; margin-top: 32px; }}
</style>
</head>
<body>
<div class="container">
  <h1>🐋 Smart Whale Tracker</h1>
  <div class="subtitle">داشبورد وضعیت ربات · به‌روزرسانی شده در {now_str}</div>

  <div class="grid kpis">
    {render_kpi("کیف‌پول‌ها", len(wallets), "#3b82f6")}
    {render_kpi("تریدهای باز", len(open_trades), "#8b5cf6")}
    {render_kpi("فروش‌ها", len(sells), "#06b6d4")}
    {render_kpi("فروش‌های سودده", len(winning_sells), "#10b981")}
    {render_kpi("نهنگ‌ها", len(active_whales), "#f59e0b")}
    {render_kpi("کاندیدها", len(candidates), "#ec4899")}
    {render_kpi("آلارم‌ها", len(alerts), "#a855f7")}
  </div>

  <div class="section">
    <h2>🏆 ۲۰ کیف‌پول برتر</h2>
    {render_wallet_table(top_wallets, 20)}
  </div>

  <div class="grid" style="grid-template-columns: 1fr 1fr;">
    <div class="section">
      <h2>🐋 نهنگ‌های فعال</h2>
      {render_whales_table(active_whales)}
    </div>
    <div class="section">
      <h2>🥚 کاندیداهای نهنگ</h2>
      {render_candidates_table(candidates[:10])}
    </div>
  </div>

  <div class="section">
    <h2>📊 فروش‌های اخیر</h2>
    {render_recent_sells(sells, 20)}
  </div>

  <div class="section">
    {render_nightly_chart(nightly_log)}
  </div>

  <footer>
    Generated by Smart Whale Tracker v{version.get_version()} · {now_str}
  </footer>
</div>
</body>
</html>
"""


def main():
    out = os.path.join(cfg.DATA_DIR, "dashboard.html")
    html_content = generate_dashboard()
    with open(out, "w", encoding="utf-8") as f:
        f.write(html_content)
    size_kb = len(html_content) / 1024
    print(f"✅ Dashboard generated: {out} ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
