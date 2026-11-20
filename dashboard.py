"""Writes dashboard.html (auto-refreshes every 30s). Open it in any browser."""
import html
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PATH = os.environ.get("DASHBOARD_PATH") or os.path.join(HERE, "dashboard.html")


def _money(x):
    x = float(x)
    return f"{'-' if x < 0 else ''}${abs(x):,.2f}"


def write(status, acct, positions, decisions, alerts, budget):
    e = html.escape
    pnl = float(acct["equity"]) - float(acct["last_equity"]) if acct else 0.0
    pos_rows = "".join(
        f"<tr><td>{e(p['symbol'])}</td><td>{p['qty']}</td><td>{_money(p['avg_entry_price'])}</td>"
        f"<td>{_money(p['current_price'])}</td><td class='{'up' if float(p['unrealized_pl']) >= 0 else 'down'}'>"
        f"{_money(p['unrealized_pl'])}</td></tr>" for p in positions
    ) or "<tr><td colspan=5 class=muted>No open positions</td></tr>"
    dec_rows = "".join(
        f"<tr><td>{e(d['time'])}</td><td><b>{e(d['symbol'])}</b></td><td class='{ 'up' if d['action']=='BUY' else 'muted'}'>{e(d['action'])}</td>"
        f"<td>{e(d.get('scanner',''))}</td><td>{e(d.get('news',''))}</td><td>{e(d.get('time_machine',''))}</td><td>{e(d.get('risk',''))}</td></tr>"
        for d in reversed(decisions[-40:])
    ) or "<tr><td colspan=7 class=muted>No trade ideas yet today</td></tr>"
    news_rows = "".join(
        f"<li><span class='{ 'up' if a['sentiment']>0 else 'down' if a['sentiment']<0 else 'muted'}'>●</span> "
        f"<b>{e(', '.join(a['symbols'][:3]))}</b> {e(a['headline'])} <span class=muted>({e(a['source'])})</span></li>"
        for a in alerts[-25:][::-1]
    ) or "<li class=muted>No headlines yet</li>"
    page = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="30">
<title>Trading Agents</title><style>
body{{font-family:Segoe UI,system-ui,sans-serif;background:#0f1218;color:#e6e8eb;margin:24px}}
h1{{margin:0 0 4px}} .muted{{color:#8a919c}} .up{{color:#3ecf8e}} .down{{color:#ff6b6b}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:16px 0}}
.card{{background:#181c24;border:1px solid #262b35;border-radius:10px;padding:14px 18px;min-width:160px}}
.card .v{{font-size:24px;font-weight:600}} table{{width:100%;border-collapse:collapse;font-size:13px}}
td,th{{padding:7px 8px;border-bottom:1px solid #232833;text-align:left;vertical-align:top}}
th{{color:#8a919c;font-weight:500}} section{{background:#181c24;border:1px solid #262b35;border-radius:10px;padding:14px;margin-bottom:16px}}
ul{{margin:0;padding-left:4px;list-style:none;font-size:13px}} li{{padding:4px 0}}
</style></head><body>
<h1>Trading Agents <span class=muted style="font-size:14px">PAPER</span></h1>
<div class=muted>{e(status)}</div>
<div class=cards>
<div class=card><div class=muted>Today's P&amp;L</div><div class="v {'up' if pnl>=0 else 'down'}">{_money(pnl)}</div></div>
<div class=card><div class=muted>Bot budget</div><div class=v>{_money(budget)}</div></div>
<div class=card><div class=muted>Paper equity</div><div class=v>{_money(acct['equity']) if acct else '-'}</div></div>
<div class=card><div class=muted>Open positions</div><div class=v>{len(positions)}</div></div>
</div>
<section><h3>Open positions</h3><table><tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Price</th><th>P/L</th></tr>{pos_rows}</table></section>
<section><h3>Agent decisions</h3><table><tr><th>Time (ET)</th><th>Symbol</th><th>Action</th><th>Chart scanner</th><th>News monitor</th><th>Time machine</th><th>Risk manager</th></tr>{dec_rows}</table></section>
<section><h3>News monitor</h3><ul>{news_rows}</ul></section>
<p class=muted><a style="color:#4ea1ff" href="backtest-v2.html">Backtest report</a> · Built by Jackson Chatriand</p>
</body></html>"""
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w", encoding="utf-8") as f:
        f.write(page)
