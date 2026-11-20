"""Backtest: runs the same agent rules on the past year of 1-minute data.

Usage:  py backtest.py            (last 12 months)
        py backtest.py --months 6
Downloads data once into backtest_cache/ (reruns are fast), then writes
backtest_report.html and backtest_trades.csv in this folder.

Not simulated: the news monitor (no historical headline check), so results
include trades the news agent might have blocked.
"""
import csv
import datetime as dt
import json
import math
import os
import random
import sys
import time

import config
from agents import time_machine

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "backtest_cache")
LOG_DIR = os.path.join(HERE, "logs")
PROGRESS = os.path.join(LOG_DIR, "backtest_progress.txt")

SLIPPAGE_PCT = 0.03      # % worse fill on every entry and exit (paper fills are kinder than this)
BT_SIMS = 2000           # time machine paths per idea in the backtest (live uses 10,000)
COOLDOWN_MIN = 15
OPEN_MIN = 9 * 60 + 30
NO_NEW = int(config.NO_NEW_TRADES_AFTER[:2]) * 60 + int(config.NO_NEW_TRADES_AFTER[3:])
FLAT = int(config.FLATTEN_AT[:2]) * 60 + int(config.FLATTEN_AT[3:])


def progress(msg):
    line = f"[{dt.datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(PROGRESS, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------- time zone
def _nth_sunday(year, month, n):
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def et_offset_hours(utc):
    """US Eastern offset for a UTC datetime (DST: 2nd Sun Mar 7:00Z .. 1st Sun Nov 6:00Z)."""
    y = utc.year
    start = dt.datetime.combine(_nth_sunday(y, 3, 2), dt.time(7))
    end = dt.datetime.combine(_nth_sunday(y, 11, 1), dt.time(6))
    return -4 if start <= utc.replace(tzinfo=None) < end else -5


# ---------------------------------------------------------------- data
def month_ranges(months):
    end = dt.date.today() - dt.timedelta(days=1)
    start = (end.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    for _ in range(months - 2):
        start = (start - dt.timedelta(days=1)).replace(day=1)
    # include the extra week before the first month for the time machine history
    out, cur = [], start
    while cur <= end:
        nxt = (cur.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        out.append((cur, min(nxt - dt.timedelta(days=1), end)))
        cur = nxt
    return out


def fetch_month(symbols, first, last, feed):
    """Download 1-min bars for one month, cached as one JSON file per month."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{first:%Y-%m}_{feed}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("last") == last.isoformat() and set(cached["bars"]) >= set(symbols) - set(cached.get("missing", [])):
            return cached["bars"]
    import alpaca_api as api
    bars, token, pages = {}, None, 0
    while True:
        r = api.data("/v2/stocks/bars", {
            "symbols": ",".join(symbols), "timeframe": "1Min",
            "start": f"{first.isoformat()}T00:00:00Z", "end": f"{last.isoformat()}T23:59:00Z",
            "feed": feed, "limit": 10000, "adjustment": "split", "page_token": token,
        })
        for sym, bs in (r.get("bars") or {}).items():
            bars.setdefault(sym, []).extend([b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]] for b in bs)
        token, pages = r.get("next_page_token"), pages + 1
        if pages % 10 == 0:
            progress(f"  {first:%b %Y}: {pages} pages downloaded...")
        if not token:
            break
        time.sleep(0.3)  # stay well under the 200 requests/minute limit
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"last": last.isoformat(), "bars": bars,
                   "missing": [s for s in symbols if s not in bars]}, f)
    return bars


def split_days(raw):
    """{sym: [[t,o,h,l,c,v],...]} -> {date: {sym: [(minute_et,o,h,l,c,v), ...]}} (regular hours only)."""
    days = {}
    for sym, rows in raw.items():
        for t, o, h, l, c, v in rows:
            utc = dt.datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S")
            et = utc + dt.timedelta(hours=et_offset_hours(utc))
            m = et.hour * 60 + et.minute
            if OPEN_MIN <= m < 16 * 60:
                days.setdefault(et.date(), {}).setdefault(sym, []).append((m, o, h, l, c, v))
    return days


# ---------------------------------------------------------------- indicators (same math as agents/scanner.py)
class DayIndicators:
    def __init__(self, bars):
        self.b = bars
        n = len(bars)
        self.e9, self.e21, self.vw, self.atr, self.vavg = [0.0] * n, [0.0] * n, [0.0] * n, [0.0] * n, [0.0] * n
        k9, k21 = 2 / 10, 2 / 22
        pv = vol = 0.0
        trs = []
        for i, (_, o, h, l, c, v) in enumerate(bars):
            self.e9[i] = c if i == 0 else c * k9 + self.e9[i - 1] * (1 - k9)
            self.e21[i] = c if i == 0 else c * k21 + self.e21[i - 1] * (1 - k21)
            pv += ((h + l + c) / 3) * v
            vol += v
            self.vw[i] = pv / vol if vol else c
            if i >= 1:
                pc = bars[i - 1][4]
                trs.append(max(h - l, abs(h - pc), abs(l - pc)))
                rec = trs[-14:]
                self.atr[i] = sum(rec) / len(rec)
            prev = [x[5] for x in bars[max(0, i - 20):i]]
            self.vavg[i] = sum(prev) / len(prev) if prev else 0.0
        orb = bars[:config.OPENING_RANGE_MIN]
        self.or_high = max(x[2] for x in orb) if orb else 0
        self.or_low = min(x[3] for x in orb) if orb else 0

    def signal(self, i):
        if i < config.OPENING_RANGE_MIN + 9:
            return None
        b = self.b
        price = b[i][4]
        if price < 3:
            return None
        orh, vw, a = self.or_high, self.vw[i], self.atr[i]
        vol_ratio = b[i][5] / self.vavg[i] if self.vavg[i] else 0
        fresh = all(x[4] <= orh for x in b[i - 5:i - 2]) and any(x[4] > orh for x in b[i - 2:i + 1])
        if not (price > orh and fresh and price > vw and self.e9[i] > self.e21[i] and vol_ratio >= 1.5):
            return None
        if a <= 0:
            return None
        stop = max(price - config.STOP_ATR_MULT * a, self.or_low)
        risk = price - stop
        if risk <= price * 0.001:
            return None
        return {"entry": round(price, 2), "stop": round(stop, 2),
                "target": round(price + config.V1_REWARD_RISK * risk, 2),
                "score": vol_ratio + (price - vw) / a, "vol_ratio": vol_ratio}


def size(entry, stop):
    risk_dollars = config.BUDGET * config.RISK_PER_TRADE_PCT / 100
    qty = math.floor(risk_dollars / (entry - stop)) if entry > stop else 0
    return max(0, min(qty, math.floor(config.BUDGET * config.MAX_POSITION_PCT / 100 / entry)))


# ---------------------------------------------------------------- one trading day
def run_day(day, sym_bars, history, use_time_machine):
    slip = SLIPPAGE_PCT / 100
    ind = {s: DayIndicators(bs) for s, bs in sym_bars.items() if len(bs) >= 25}
    pos_at = {s: {b[0]: i for i, b in enumerate(bs)} for s, bs in sym_bars.items()}
    open_pos, pending, trades = {}, [], []
    cooldown, trades_today, realized, halted = {}, 0, 0.0, False
    budget = config.BUDGET

    def close(sym, p, price, minute, reason):
        nonlocal realized
        fill = price * (1 - slip)
        pnl = (fill - p["fill"]) * p["qty"]
        realized += pnl
        trades.append({"date": day.isoformat(), "symbol": sym, "entry_time": _hhmm(p["minute"]),
                       "exit_time": _hhmm(minute), "qty": p["qty"], "entry": round(p["fill"], 4),
                       "exit": round(fill, 4), "stop": p["stop"], "target": p["target"],
                       "reason": reason, "pnl": round(pnl, 2),
                       "r": round(pnl / (p["qty"] * (p["sig_entry"] - p["stop"])), 3),
                       "p_target": p.get("p_target")})

    for m in range(OPEN_MIN, 16 * 60):
        # 1) fill entries decided last minute at this bar's open
        for sym, sig, qty, pt in pending:
            i = pos_at[sym].get(m)
            if i is None:
                continue  # no trade this minute; skip the idea (market order would fill later, keep it simple)
            o = sym_bars[sym][i][1]
            open_pos[sym] = {"fill": o * (1 + slip), "qty": qty, "stop": sig["stop"], "target": sig["target"],
                             "sig_entry": sig["entry"], "minute": m, "p_target": pt}
        pending = []

        # 2) bracket exits on this bar
        for sym in list(open_pos):
            i = pos_at[sym].get(m)
            if i is None:
                continue
            _, o, h, l, c, v = sym_bars[sym][i]
            p = open_pos[sym]
            if m >= FLAT:
                close(sym, p, o, m, "close"); del open_pos[sym]; continue
            if l <= p["stop"]:
                close(sym, p, min(o, p["stop"]), m, "stop"); del open_pos[sym]
            elif h >= p["target"]:
                close(sym, p, max(o, p["target"]), m, "target"); del open_pos[sym]

        # 3) daily loss limit (realized + open marks)
        unreal = 0.0
        for sym, p in open_pos.items():
            i = pos_at[sym].get(m)
            last = sym_bars[sym][i][4] if i is not None else p["fill"]
            unreal += (last - p["fill"]) * p["qty"]
        if realized + unreal <= -budget * config.DAILY_LOSS_LIMIT_PCT / 100:
            halted = True

        # 4) new ideas (the live bot sees this bar a moment after it closes)
        clock = m + 1
        if halted or clock >= NO_NEW or trades_today >= config.MAX_TRADES_PER_DAY:
            continue
        sigs = []
        for sym, d in ind.items():
            i = pos_at[sym].get(m)
            if i is None:
                continue
            s = d.signal(i)
            if s:
                sigs.append((s["score"], sym, s, i))
        sigs.sort(reverse=True)
        for _, sym, s, i in sigs[:3]:
            if clock - cooldown.get(sym, -999) < COOLDOWN_MIN:
                continue
            cooldown[sym] = clock
            pt = None
            if use_time_machine:
                rets = time_machine.minute_returns(history.get(sym, []) + [{"c": b[4]} for b in sym_bars[sym][:i + 1]])
                sim = time_machine.simulate(s["entry"], s["stop"], s["target"], rets, FLAT - clock,
                                            n=BT_SIMS, seed=day.toordinal() * 100000 + m * 50 + sum(map(ord, sym)))
                ok, _ = time_machine.approve(sim)
                if not ok:
                    continue
                pt = sim["p_target"]
            if sym in open_pos or any(x[0] == sym for x in pending):
                continue
            if len(open_pos) + len(pending) >= config.MAX_OPEN_POSITIONS:
                continue
            qty = size(s["entry"], s["stop"])
            if qty < 1:
                continue
            pending.append((sym, s, qty, pt))
            trades_today += 1

    # anything still open (half days / missing bars): exit at the last bar
    for sym, p in open_pos.items():
        last = sym_bars[sym][-1]
        close(sym, p, last[4], last[0], "close")
    return trades


def _hhmm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


# ---------------------------------------------------------------- stats + report
def stats(trades, days):
    pnl = [t["pnl"] for t in trades]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x <= 0]
    daily = {}
    for t in trades:
        daily[t["date"]] = daily.get(t["date"], 0) + t["pnl"]
    eq, peak, mdd, curve = config.BUDGET, config.BUDGET, 0.0, []
    for d in days:
        eq += daily.get(d, 0)
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
        curve.append((d, eq))
    rs = [t["r"] for t in trades]
    return {
        "trades": len(trades), "days": len(days),
        "win_rate": len(wins) / len(trades) if trades else 0,
        "total_pnl": sum(pnl), "return_pct": sum(pnl) / config.BUDGET * 100,
        "avg_win": sum(wins) / len(wins) if wins else 0, "avg_loss": sum(losses) / len(losses) if losses else 0,
        "profit_factor": sum(wins) / -sum(losses) if losses and sum(losses) else float("inf"),
        "avg_r": sum(rs) / len(rs) if rs else 0, "max_dd": mdd,
        "by_reason": {k: sum(1 for t in trades if t["reason"] == k) for k in ("target", "stop", "close")},
        "curve": curve, "daily": daily,
    }


def svg_curve(curves, w=860, h=260):
    allv = [v for _, c in curves for _, v in c]
    if not allv:
        return ""
    lo, hi = min(allv), max(allv)
    pad = (hi - lo) * 0.08 or 50
    lo, hi = lo - pad, hi + pad
    n = max(len(c) for _, c in curves)
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px">']
    y0 = h - (config.BUDGET - lo) / (hi - lo) * h
    out.append(f'<line x1="0" x2="{w}" y1="{y0:.1f}" y2="{y0:.1f}" stroke="#3a404c" stroke-dasharray="4 4"/>')
    for color, c in curves:
        pts = " ".join(f"{i / max(1, n - 1) * w:.1f},{h - (v - lo) / (hi - lo) * h:.1f}" for i, (_, v) in enumerate(c))
        out.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{pts}"/>')
    out.append(f'<text x="4" y="14" fill="#8a919c" font-size="11">${hi:,.0f}</text>'
               f'<text x="4" y="{h - 4}" fill="#8a919c" font-size="11">${lo:,.0f}</text></svg>')
    return "".join(out)


def money(x):
    return f"{'-' if x < 0 else ''}${abs(x):,.2f}"


def write_report(full, base, trades, first, last, feed, elapsed):
    def row(label, a, b, fmt):
        return f"<tr><td>{label}</td><td><b>{fmt(a)}</b></td><td>{fmt(b)}</td></tr>"
    pct = lambda x: f"{x:.1%}"
    num = lambda x: f"{x:,.0f}"
    pf = lambda x: "∞" if x == float("inf") else f"{x:.2f}"
    rr = lambda x: f"{x:+.3f}R"
    months = {}
    for t in trades:
        k = t["date"][:7]
        months.setdefault(k, [0, 0, 0.0])
        months[k][0] += 1
        months[k][1] += t["pnl"] > 0
        months[k][2] += t["pnl"]
    month_rows = "".join(f"<tr><td>{k}</td><td>{v[0]}</td><td>{v[1] / v[0]:.0%}</td>"
                         f"<td class='{'up' if v[2] >= 0 else 'down'}'>{money(v[2])}</td></tr>"
                         for k, v in sorted(months.items()))
    syms = {}
    for t in trades:
        syms.setdefault(t["symbol"], [0, 0.0])
        syms[t["symbol"]][0] += 1
        syms[t["symbol"]][1] += t["pnl"]
    sym_rows = "".join(f"<tr><td>{k}</td><td>{v[0]}</td><td class='{'up' if v[1] >= 0 else 'down'}'>{money(v[1])}</td></tr>"
                       for k, v in sorted(syms.items(), key=lambda kv: kv[1][1], reverse=True))
    trade_rows = "".join(
        f"<tr><td>{t['date']}</td><td>{t['symbol']}</td><td>{t['entry_time']}</td><td>{t['exit_time']}</td>"
        f"<td>{t['qty']}</td><td>{t['entry']:.2f}</td><td>{t['exit']:.2f}</td><td>{t['reason']}</td>"
        f"<td class='{'up' if t['pnl'] >= 0 else 'down'}'>{money(t['pnl'])}</td><td>{t['r']:+.2f}</td></tr>"
        for t in reversed(trades[-300:]))
    verdict_good = full["total_pnl"] > 0 and full["profit_factor"] > 1.2 and full["trades"] >= 50
    verdict = ("Profitable in the backtest after slippage. Promising, but confirm with a month or two of paper trading before any real money."
               if verdict_good else
               "Not profitable enough in the backtest after slippage. Don't trade this with real money as it stands; the rules need changing first."
               if full["trades"] >= 20 else
               "Too few trades to judge. The scanner rules may be too strict.")
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Backtest report</title><style>
body{{font-family:Segoe UI,system-ui,sans-serif;background:#0f1218;color:#e6e8eb;margin:24px;max-width:1000px}}
.muted{{color:#8a919c}} .up{{color:#3ecf8e}} .down{{color:#ff6b6b}}
section{{background:#181c24;border:1px solid #262b35;border-radius:10px;padding:16px;margin:16px 0}}
table{{width:100%;border-collapse:collapse;font-size:13px}} td,th{{padding:6px 8px;border-bottom:1px solid #232833;text-align:left}}
th{{color:#8a919c;font-weight:500}} .cards{{display:flex;gap:12px;flex-wrap:wrap}}
.card{{background:#181c24;border:1px solid #262b35;border-radius:10px;padding:14px 18px;min-width:150px}} .v{{font-size:24px;font-weight:600}}
.key span{{display:inline-block;width:12px;height:3px;margin:0 6px 3px 12px;vertical-align:middle}}
</style></head><body>
<h1>Backtest: trading agents</h1>
<div class=muted>{first} to {last} · 1-minute bars ({feed.upper()} feed) · {len(config.WATCHLIST)} stocks · budget {money(config.BUDGET)} · slippage {SLIPPAGE_PCT}% per fill · ran in {elapsed / 60:.1f} min</div>
<section><b>Verdict:</b> {verdict}</section>
<div class=cards>
<div class=card><div class=muted>Total P&amp;L</div><div class="v {'up' if full['total_pnl'] >= 0 else 'down'}">{money(full['total_pnl'])}</div><div class=muted>{full['return_pct']:+.1f}% of budget</div></div>
<div class=card><div class=muted>Trades</div><div class=v>{full['trades']}</div><div class=muted>over {full['days']} days</div></div>
<div class=card><div class=muted>Win rate</div><div class=v>{full['win_rate']:.0%}</div><div class=muted>break-even ≈ 34%</div></div>
<div class=card><div class=muted>Profit factor</div><div class=v>{pf(full['profit_factor'])}</div><div class=muted>above 1.0 = profitable</div></div>
<div class=card><div class=muted>Max drawdown</div><div class="v down">{money(full['max_dd'])}</div></div>
</div>
<section><h3>Equity curve</h3><div class="key muted"><span style="background:#4ea1ff"></span>all agents<span style="background:#8a919c"></span>scanner + risk only (no time machine)</div>
{svg_curve([("#8a919c", base['curve']), ("#4ea1ff", full['curve'])])}</section>
<section><h3>Does the time machine help?</h3><table><tr><th></th><th>All agents</th><th>Without time machine</th></tr>
{row("Trades", full['trades'], base['trades'], num)}{row("Win rate", full['win_rate'], base['win_rate'], pct)}
{row("Total P&amp;L", full['total_pnl'], base['total_pnl'], money)}{row("Profit factor", full['profit_factor'], base['profit_factor'], pf)}
{row("Avg result per trade", full['avg_r'], base['avg_r'], rr)}{row("Avg win", full['avg_win'], base['avg_win'], money)}
{row("Avg loss", full['avg_loss'], base['avg_loss'], money)}{row("Max drawdown", full['max_dd'], base['max_dd'], money)}
</table><p class=muted>Exits (all agents): {full['by_reason']['target']} hit target · {full['by_reason']['stop']} hit stop · {full['by_reason']['close']} closed at end of day</p></section>
<section><h3>By month</h3><table><tr><th>Month</th><th>Trades</th><th>Win rate</th><th>P&amp;L</th></tr>{month_rows}</table></section>
<section><h3>By stock</h3><table><tr><th>Stock</th><th>Trades</th><th>P&amp;L</th></tr>{sym_rows}</table></section>
<section><h3>Trades (latest 300)</h3><table><tr><th>Date</th><th>Stock</th><th>In</th><th>Out</th><th>Qty</th><th>Entry</th><th>Exit</th><th>Exit by</th><th>P&amp;L</th><th>R</th></tr>{trade_rows}</table></section>
<p class=muted>Not simulated: the news monitor. Fills assume the next minute's open plus slippage; if a bar touches both stop and target, the stop is assumed. Past results don't guarantee future ones.</p>
</body></html>"""
    with open(os.path.join(HERE, "backtest_report.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(HERE, "backtest_trades.csv"), "w", newline="", encoding="utf-8") as f:
        if trades:
            w = csv.DictWriter(f, fieldnames=list(trades[0]))
            w.writeheader()
            w.writerows(trades)
    summary = {k: v for k, v in full.items() if k not in ("curve", "daily")}
    summary["baseline"] = {k: v for k, v in base.items() if k not in ("curve", "daily")}
    summary.update({"first": str(first), "last": str(last), "feed": feed})
    with open(os.path.join(LOG_DIR, "backtest_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)


# ---------------------------------------------------------------- main
def run(months=12, feed=None, loader=None):
    t0 = time.time()
    if os.path.exists(PROGRESS):
        os.remove(PROGRESS)
    feed = feed or "sip"
    ranges = month_ranges(months)
    progress(f"Backtest {ranges[0][0]} to {ranges[-1][1]} on {len(config.WATCHLIST)} stocks")
    all_full, all_base, all_days = [], [], []
    history = {}
    for first, last in ranges:
        if loader:
            raw = loader(first, last)
        else:
            try:
                raw = fetch_month(config.WATCHLIST, first, last, feed)
            except Exception as e:
                if feed == "sip":
                    progress(f"SIP data not available ({str(e)[:120]}). Switching to the free IEX feed.")
                    feed = "iex"
                    raw = fetch_month(config.WATCHLIST, first, last, feed)
                else:
                    raise
        days = split_days(raw)
        progress(f"{first:%b %Y}: {len(days)} trading days, simulating...")
        for day in sorted(days):
            sym_bars = days[day]
            if day >= ranges[0][0] and history:
                all_full += run_day(day, sym_bars, history, True)
                all_base += run_day(day, sym_bars, history, False)
                all_days.append(day.isoformat())
            # keep about the last 5 days of closes per symbol for the time machine
            for sym, bs in sym_bars.items():
                h = history.setdefault(sym, [])
                h.extend({"c": b[4]} for b in bs)
                del h[:-2000]
        progress(f"{first:%b %Y}: done. Running total {len(all_full)} trades, P&L {money(sum(t['pnl'] for t in all_full))}")
    full, base = stats(all_full, all_days), stats(all_base, all_days)
    write_report(full, base, all_full, all_days[0] if all_days else "-", all_days[-1] if all_days else "-",
                 feed, time.time() - t0)
    progress(f"DONE. {full['trades']} trades, win rate {full['win_rate']:.0%}, P&L {money(full['total_pnl'])}, "
             f"profit factor {full['profit_factor']:.2f}. Report: backtest_report.html")
    return full, base


if __name__ == "__main__":
    months = 12
    if "--months" in sys.argv:
        months = int(sys.argv[sys.argv.index("--months") + 1])
    feed = "iex" if "--iex" in sys.argv else "sip"
    try:
        run(months, feed)
        if os.name == "nt":
            os.startfile(os.path.join(HERE, "backtest_report.html"))
    except Exception as e:
        import traceback
        progress("ERROR: " + str(e))
        traceback.print_exc()
