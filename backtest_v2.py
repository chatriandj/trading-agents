"""Version 2 backtest: fewer, bigger trades on 'stocks in play'.

Changes from version 1:
  - 5-minute bars, opening range = first 5 minutes (9:30-9:35 ET)
  - Only "stocks in play": opening volume >= N x the stock's own 10-day average
  - Only trade when the market (SPY) is above its open and its VWAP
  - Max 2 trades a day, 1 per stock, risk $ = 0.5% of budget (position cap = full budget)
  - Wider stops and exits that let winners run (several versions tested)

Honest testing: every rule combination is tuned on the TRAIN months only
(Dec 2025 - Jul 2026). The single best one is then run once on the TEST months
(Aug - Nov 2026), which it never saw. Uses the data backtest.py already downloaded.
"""
import csv
import datetime as dt
import glob
import itertools
import json
import math
import os
import time

import backtest as bt
import config

HERE = os.path.dirname(os.path.abspath(__file__))
TEST_START = dt.date(2026, 8, 1)
ETFS = {"SPY", "QQQ", "IWM"}
SLIP = bt.SLIPPAGE_PCT / 100
RISK_DOLLARS = config.BUDGET * config.RISK_PER_TRADE_PCT / 100
MAX_TRADES = 2
TOP_N = 5
LAST_ENTRY = 15 * 60 + 30
FLAT = 15 * 60 + 50

GRID = {
    "rvol": [1.5, 2.5],                       # opening volume vs 10-day average
    "stop": ["or_low", "atr20", "atr35"],     # opening-range low, or 20% / 35% of daily ATR
    "exit": ["target3R", "trail", "close"],   # 3:1 target, trailing stop, or hold to close
}


# ---------------------------------------------------------------- data prep
def to_5min(bars):
    out, cur = [], None
    for m, o, h, l, c, v in bars:
        b = m - (m - bt.OPEN_MIN) % 5
        if cur and cur[0] == b:
            cur[2], cur[3], cur[4], cur[5] = max(cur[2], h), min(cur[3], l), c, cur[5] + v
        else:
            if cur:
                out.append(tuple(cur))
            cur = [b, o, h, l, c, v]
    if cur:
        out.append(tuple(cur))
    return out


def load_days():
    files = sorted(glob.glob(os.path.join(bt.CACHE, "*_sip.json"))) or sorted(glob.glob(os.path.join(bt.CACHE, "*_iex.json")))
    if not files:
        raise SystemExit("No downloaded data found. Run backtest.bat first.")
    days = {}
    for path in files:
        bt.progress(f"Loading {os.path.basename(path)}")
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)["bars"]
        for day, syms in bt.split_days(raw).items():
            days[day] = {s: to_5min(b) for s, b in syms.items() if b}
    return days


def add_context(days):
    """Per day & stock: 14-day ATR and 10-day avg opening volume from PRIOR days only."""
    hist = {}
    ctx = {}
    for day in sorted(days):
        ctx[day] = {}
        for s, b5 in days[day].items():
            h = hist.setdefault(s, {"tr": [], "ov": [], "pc": None})
            if len(h["tr"]) >= 10 and len(h["ov"]) >= 5 and b5 and b5[0][0] == bt.OPEN_MIN:
                ctx[day][s] = {"atr": sum(h["tr"][-14:]) / len(h["tr"][-14:]),
                               "rvol": b5[0][5] / (sum(h["ov"][-10:]) / len(h["ov"][-10:]) or 1)}
            hi, lo, cl = max(x[2] for x in b5), min(x[3] for x in b5), b5[-1][4]
            pc = h["pc"] if h["pc"] is not None else b5[0][1]
            h["tr"].append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
            if b5[0][0] == bt.OPEN_MIN:
                h["ov"].append(b5[0][5])
            h["pc"] = cl
    return ctx


def vwap_series(b5):
    pv = vol = 0.0
    out = []
    for _, o, h, l, c, v in b5:
        pv += (h + l + c) / 3 * v
        vol += v
        out.append(pv / vol if vol else c)
    return out


# ---------------------------------------------------------------- one day, one rule set
def run_day(day, bars, ctx, p):
    spy = bars.get("SPY")
    if not spy:
        return []
    spy_at = {b[0]: (b[4], vw, spy[0][1]) for b, vw in zip(spy, vwap_series(spy))}
    cands = sorted(((c["rvol"], s) for s, c in ctx.items()
                    if s not in ETFS and c["rvol"] >= p["rvol"] and s in bars and bars[s][0][1] >= 5),
                   reverse=True)[:TOP_N]
    if not cands:
        return []
    info = {}
    for _, s in cands:
        b5 = bars[s]
        info[s] = {"b": b5, "vw": vwap_series(b5), "idx": {x[0]: i for i, x in enumerate(b5)},
                   "orh": b5[0][2], "orl": b5[0][3], "atr": ctx[s]["atr"], "rvol": ctx[s]["rvol"], "broke": False}
    trades, open_pos, pending, n_trades = [], {}, [], 0
    for m in range(bt.OPEN_MIN + 5, 16 * 60, 5):
        # fills at this bar's open
        for s, sig_entry, stop, qty in pending:
            i = info[s]["idx"].get(m)
            if i is None:
                continue
            fill = info[s]["b"][i][1] * (1 + SLIP)
            r = sig_entry - stop
            open_pos[s] = {"fill": fill, "stop": stop, "qty": qty, "r": r, "sig": sig_entry, "m": m,
                           "target": sig_entry + 3 * r, "hh": fill, "init_stop": stop}
        pending = []
        # manage open positions on this bar
        for s in list(open_pos):
            i = info[s]["idx"].get(m)
            if i is None:
                continue
            _, o, h, l, c, v = info[s]["b"][i]
            pos = open_pos[s]
            exit_px = reason = None
            if m >= FLAT:
                exit_px, reason = o, "close"
            elif l <= pos["stop"]:
                exit_px, reason = min(o, pos["stop"]), "stop"
            elif p["exit"] == "target3R" and h >= pos["target"]:
                exit_px, reason = max(o, pos["target"]), "target"
            if exit_px is not None:
                fill = exit_px * (1 - SLIP)
                pnl = (fill - pos["fill"]) * pos["qty"]
                trades.append({"date": day.isoformat(), "symbol": s, "entry_time": bt._hhmm(pos["m"]),
                               "exit_time": bt._hhmm(m), "qty": pos["qty"], "entry": round(pos["fill"], 4),
                               "exit": round(fill, 4), "stop": round(pos["init_stop"], 2),
                               "target": round(pos["target"], 2) if p["exit"] == "target3R" else "",
                               "reason": reason, "pnl": round(pnl, 2),
                               "r": round(pnl / (pos["qty"] * pos["r"]), 3), "p_target": ""})
                del open_pos[s]
                continue
            if p["exit"] == "trail":  # after +1R: stop to breakeven, then trail 1R under the high
                pos["hh"] = max(pos["hh"], h)
                if pos["hh"] >= pos["sig"] + pos["r"]:
                    pos["stop"] = max(pos["stop"], pos["sig"], pos["hh"] - pos["r"])
        # new signals at this bar's close (enter next bar)
        if n_trades >= MAX_TRADES or m + 5 > LAST_ENTRY or len(open_pos) + len(pending) >= MAX_TRADES:
            continue
        sp = spy_at.get(m)
        if not sp or not (sp[0] > sp[1] and sp[0] > sp[2]):
            continue
        for _, s in cands:
            d = info[s]
            i = d["idx"].get(m)
            if i is None or i == 0 or d["broke"]:
                continue
            c = d["b"][i][4]
            if c > d["orh"]:
                d["broke"] = True  # only the first breakout counts
                if c <= d["vw"][i] or s in open_pos:
                    continue
                stop = {"or_low": d["orl"], "atr20": c - 0.20 * d["atr"], "atr35": c - 0.35 * d["atr"]}[p["stop"]]
                if c - stop < c * 0.0015:
                    continue
                qty = min(math.floor(RISK_DOLLARS / (c - stop)), math.floor(config.BUDGET / c))
                if qty < 1:
                    continue
                pending.append((s, c, stop, qty))
                n_trades += 1
                if n_trades >= MAX_TRADES:
                    break
    for s, pos in open_pos.items():  # half days
        last = info[s]["b"][-1]
        fill = last[4] * (1 - SLIP)
        pnl = (fill - pos["fill"]) * pos["qty"]
        trades.append({"date": day.isoformat(), "symbol": s, "entry_time": bt._hhmm(pos["m"]),
                       "exit_time": bt._hhmm(last[0]), "qty": pos["qty"], "entry": round(pos["fill"], 4),
                       "exit": round(fill, 4), "stop": round(pos["init_stop"], 2), "target": "",
                       "reason": "close", "pnl": round(pnl, 2), "r": round(pnl / (pos["qty"] * pos["r"]), 3),
                       "p_target": ""})
    return trades


def run_period(days, ctx, dates, p):
    trades = []
    for d in dates:
        trades += run_day(d, days[d], ctx[d], p)
    return trades


# ---------------------------------------------------------------- report
def label(p):
    return f"rvol ≥{p['rvol']}x · stop {p['stop'].replace('_', ' ')} · exit {p['exit']}"


def write_report(rows, best, tr_best, te_best, v1_test, v1_train, train_dates, test_dates, elapsed):
    m = bt.money
    pf = lambda x: "∞" if x == float("inf") else f"{x:.2f}"
    def col(s):
        return (f"<td>{s['trades']}</td><td>{s['win_rate']:.0%}</td><td class='{'up' if s['total_pnl'] >= 0 else 'down'}'>"
                f"{m(s['total_pnl'])}</td><td>{pf(s['profit_factor'])}</td><td>{s['avg_r']:+.2f}R</td><td>{m(s['max_dd'])}</td>")
    hl = ' style="background:#1f2a3a"'
    grid_rows = "".join(f"<tr{hl if p == best else ''}><td>{label(p)}</td>{col(s)}</tr>"
                        for p, s in sorted(rows, key=lambda r: r[1]['total_pnl'], reverse=True))
    good = te_best["total_pnl"] > 0 and te_best["profit_factor"] >= 1.3 and te_best["trades"] >= 40
    ok = te_best["total_pnl"] > 0
    verdict = ("<span class=up>Passed.</span> The best version stayed profitable on months it never saw. Next step: paper trade it for 1-2 months."
               if good else
               "<span style='color:#f5c542'>Borderline.</span> Profitable on the unseen months, but not by enough (or not enough trades) to trust yet."
               if ok else
               "<span class=down>Failed.</span> The best version from training lost money on the unseen months. Not ready for real money.")
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Backtest v2</title><style>
body{{font-family:Segoe UI,system-ui,sans-serif;background:#0f1218;color:#e6e8eb;margin:24px;max-width:1050px}}
.muted{{color:#8a919c}} .up{{color:#3ecf8e}} .down{{color:#ff6b6b}}
section{{background:#181c24;border:1px solid #262b35;border-radius:10px;padding:16px;margin:16px 0}}
table{{width:100%;border-collapse:collapse;font-size:13px}} td,th{{padding:6px 8px;border-bottom:1px solid #232833;text-align:left}}
th{{color:#8a919c;font-weight:500}} .key span{{display:inline-block;width:12px;height:3px;margin:0 6px 3px 12px;vertical-align:middle}}
</style></head><body>
<h1>Backtest v2: stocks in play</h1>
<div class=muted>Train {train_dates[0]} to {train_dates[-1]} · Test {test_dates[0]} to {test_dates[-1]} · 5-minute bars · budget {m(config.BUDGET)} · ${RISK_DOLLARS:.0f} risk per trade · slippage {bt.SLIPPAGE_PCT}% per fill · {len(rows)} rule combinations · ran in {elapsed / 60:.1f} min</div>
<section><b>Verdict:</b> {verdict}<br><span class=muted>Chosen on training data only: {label(best)}</span></section>
<section><h3>Version 1 vs version 2</h3><table><tr><th></th><th>Trades</th><th>Win rate</th><th>P&amp;L</th><th>Profit factor</th><th>Avg/trade</th><th>Max drawdown</th></tr>
<tr><td>v1 · train months</td>{col(v1_train)}</tr><tr><td><b>v2 · train months</b></td>{col(tr_best)}</tr>
<tr><td>v1 · test months</td>{col(v1_test)}</tr><tr><td><b>v2 · test months (unseen)</b></td>{col(te_best)}</tr></table></section>
<section><h3>Equity curve, v2 chosen rules</h3><div class="key muted"><span style="background:#4ea1ff"></span>train<span style="background:#f5c542"></span>test (unseen)</div>
{bt.svg_curve([("#4ea1ff", tr_best['curve']), ("#f5c542", te_best['curve'])])}
<p class=muted>Both curves start at {m(config.BUDGET)}.</p></section>
<section><h3>Every rule combination on the training months</h3><table><tr><th>Rules</th><th>Trades</th><th>Win rate</th><th>P&amp;L</th><th>Profit factor</th><th>Avg/trade</th><th>Max drawdown</th></tr>{grid_rows}</table>
<p class=muted>Highlighted = chosen (best profit factor with at least 60 trades). Only the chosen one was run on the test months.</p></section>
<p class=muted>Not simulated: the news monitor. Universe is the same 40 stocks as v1 (a real stocks-in-play scan would cover the whole market). If a 5-minute bar touches the stop, the stop is assumed hit before anything else.</p>
</body></html>"""
    with open(os.path.join(HERE, "backtest_v2_report.html"), "w", encoding="utf-8") as f:
        f.write(html)


def v1_split(train_dates, test_dates):
    path = os.path.join(HERE, "backtest_trades.csv")
    tr, te = [], []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                t = {"date": r["date"], "pnl": float(r["pnl"]), "r": float(r["r"]), "reason": r["reason"]}
                (te if dt.date.fromisoformat(r["date"]) >= TEST_START else tr).append(t)
    return bt.stats(tr, [d.isoformat() for d in train_dates]), bt.stats(te, [d.isoformat() for d in test_dates])


def main():
    t0 = time.time()
    if os.path.exists(bt.PROGRESS):
        os.remove(bt.PROGRESS)
    days = load_days()
    ctx = add_context(days)
    dates = sorted(d for d in days if ctx[d])
    train = [d for d in dates if d < TEST_START]
    test = [d for d in dates if d >= TEST_START]
    bt.progress(f"Train {len(train)} days, test {len(test)} days. Testing rule combinations on train...")
    rows = []
    for rv, st, ex in itertools.product(GRID["rvol"], GRID["stop"], GRID["exit"]):
        p = {"rvol": rv, "stop": st, "exit": ex}
        s = bt.stats(run_period(days, ctx, train, p), [d.isoformat() for d in train])
        rows.append((p, s))
        bt.progress(f"  {label(p)}: {s['trades']} trades, P&L {bt.money(s['total_pnl'])}, PF {s['profit_factor']:.2f}")
    eligible = [r for r in rows if r[1]["trades"] >= 60] or rows
    best, tr_best = max(eligible, key=lambda r: (r[1]["profit_factor"], r[1]["total_pnl"]))
    bt.progress(f"Chosen on train: {label(best)}. Running it once on the unseen test months...")
    te_trades = run_period(days, ctx, test, best)
    te_best = bt.stats(te_trades, [d.isoformat() for d in test])
    v1_train, v1_test = v1_split(train, test)
    write_report(rows, best, tr_best, te_best, v1_test, v1_train, train, test, time.time() - t0)
    with open(os.path.join(HERE, "backtest_v2_trades.csv"), "w", newline="", encoding="utf-8") as f:
        if te_trades:
            w = csv.DictWriter(f, fieldnames=list(te_trades[0]))
            w.writeheader()
            w.writerows(te_trades)
    summary = {"best": best, "train": {k: v for k, v in tr_best.items() if k not in ("curve", "daily")},
               "test": {k: v for k, v in te_best.items() if k not in ("curve", "daily")},
               "v1_train": {k: v for k, v in v1_train.items() if k not in ("curve", "daily")},
               "v1_test": {k: v for k, v in v1_test.items() if k not in ("curve", "daily")},
               "grid": [{"p": p, "trades": s["trades"], "pnl": s["total_pnl"], "pf": s["profit_factor"],
                         "win": s["win_rate"]} for p, s in rows]}
    with open(os.path.join(bt.LOG_DIR, "backtest_v2_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    bt.progress(f"DONE. Test months: {te_best['trades']} trades, win rate {te_best['win_rate']:.0%}, "
                f"P&L {bt.money(te_best['total_pnl'])}, PF {te_best['profit_factor']:.2f}. Report: backtest_v2_report.html")


if __name__ == "__main__":
    try:
        main()
        if os.name == "nt":
            os.startfile(os.path.join(HERE, "backtest_v2_report.html"))
    except Exception as e:
        import traceback
        bt.progress("ERROR: " + str(e))
        traceback.print_exc()
