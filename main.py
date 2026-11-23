"""Runs the trading agents together on Alpaca paper trading (version 2 rules).
Start it with run.bat (or: python main.py). Stop it with Ctrl+C.

Every 30 seconds during market hours:
  Chart scanner  -> after 9:35 ET picks today's "stocks in play" (2.5x+ opening volume),
                    then waits for the first 5-min close above the opening high with SPY up
  News monitor   -> prints headlines and blocks a trade on bad news
  Time machine   -> simulates 10,000 rest-of-day paths and reports the odds (info only)
  Risk manager   -> sizes the trade ($50 risk), max 2 trades/day, final say
"""
import datetime as dt
import glob
import json
import os
import sys
import time
import traceback

import alpaca_api as api
import config
import dashboard
from agents import scanner, time_machine
from agents.news import NewsMonitor
from agents.risk import RiskManager

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(HERE, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
AUTO = "--auto" in sys.argv  # started by a scheduler: exit after the day's close
MAX_RUNTIME_MIN = float(os.environ.get("MAX_RUNTIME_MIN", "0"))  # GitHub Actions: exit cleanly before the 6-hour job limit
START_TIME = time.time()
OPEN_MIN = 9 * 60 + 30


def log(msg):
    stamp = dt.datetime.now().strftime("%H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    with open(os.path.join(LOG_DIR, "bot.log"), "a", encoding="utf-8") as f:
        f.write(f"{dt.date.today()} {line}\n")


def keep_awake():
    """Stop Windows from sleeping while the bot runs (released automatically when it exits)."""
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)


_lock = None


def single_instance():
    """Exit if another copy of the bot is already running."""
    global _lock
    _lock = open(os.path.join(LOG_DIR, "bot.lock"), "w")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(_lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("The bot is already running in another window.")
        sys.exit(0)


def code_stamp():
    files = glob.glob(os.path.join(HERE, "*.py")) + glob.glob(os.path.join(HERE, "agents", "*.py"))
    return {f: os.path.getmtime(f) for f in files}


def restart_if_updated(stamp):
    """If the bot's code was updated, restart it in the same window so the new rules apply."""
    if code_stamp() != stamp:
        log("Code update detected. Restarting with the new version...")
        sys.exit(3)  # run.bat / run_auto.bat start the bot again when it exits with code 3


# ---------------------------------------------------------------- time helpers
def parse_clock(ts):
    """'2026-11-19T13:31:00.123-05:00' -> (date 'YYYY-MM-DD', 'HH:MM', '-05:00')."""
    return ts[:10], ts[11:16], ts[-6:]


def to_min(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def to_hhmm(minutes):
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def session_cutoffs(clk):
    """(flatten time, no-new-trades time) in ET for today's session.
    Normal days use the settings (15:50 / 15:30). On early-close days (1:00 PM ET,
    e.g. the day after Thanksgiving and Christmas Eve) both move earlier by the same
    margin, so positions are never left open after the bell."""
    flatten_at, no_new_after = config.FLATTEN_AT, config.NO_NEW_TRADES_AFTER
    if clk.get("is_open") and clk.get("next_close"):
        close_min = to_min(parse_clock(clk["next_close"])[1])
        flatten_at = min(flatten_at, to_hhmm(close_min - 10))
        no_new_after = min(no_new_after, to_hhmm(close_min - 30))
    return flatten_at, no_new_after


def bar_et(b):
    """Bar start as (ET date, ET minute-of-day)."""
    from backtest import et_offset_hours
    utc = dt.datetime.strptime(b["t"][:19], "%Y-%m-%dT%H:%M:%S")
    et = utc + dt.timedelta(hours=et_offset_hours(utc))
    return et.date(), et.hour * 60 + et.minute


def avg_opening_volume(day):
    """10-day average volume of each stock's 9:30-9:35 bar (prior days only)."""
    start = (dt.date.fromisoformat(day) - dt.timedelta(days=21)).isoformat() + "T00:00:00Z"
    hist = api.bars(config.WATCHLIST, start, timeframe="5Min")
    out = {}
    for sym, bs in hist.items():
        vols = []
        for b in bs:
            d, m = bar_et(b)
            if m == OPEN_MIN and d.isoformat() < day:
                vols.append(b["v"])
        if len(vols) >= 5:
            out[sym] = sum(vols[-10:]) / len(vols[-10:])
    return out


def completed_today(raw, now_min):
    """{sym: [bar...]} -> only today's regular-hours bars that have fully closed."""
    out = {}
    for sym, bs in raw.items():
        keep = []
        for b in bs:
            _, m = bar_et(b)
            if OPEN_MIN <= m and m + 5 <= now_min:
                keep.append(b)
        if keep and bar_et(keep[0])[1] == OPEN_MIN:
            out[sym] = keep
    return out


def earlier_breakout(bars, spy):
    """True if a bar before the latest one already closed above the opening high while SPY was up."""
    or_high = bars[0]["h"]
    spy_idx = {b["t"]: i for i, b in enumerate(spy)}
    for b in bars[1:-1]:
        i = spy_idx.get(b["t"])
        if b["c"] > or_high and i is not None and scanner.market_ok(spy[:i + 1])[0]:
            return True
    return False


# ---------------------------------------------------------------- main loop
def startup_checks():
    if not config.API_KEY or not config.API_SECRET:
        sys.exit("Missing keys. Open keys.txt in this folder and paste your Alpaca Key and Secret.")
    if "paper-api" not in config.BASE_URL and not config.ALLOW_LIVE:
        sys.exit("Refusing to run: APCA_API_BASE_URL is not the paper endpoint. "
                 "Live trading needs ALLOW_LIVE=yes in keys.txt (don't do this until paper results hold up).")
    acct = api.account()
    log(f"Connected (strategy v2: stocks in play). Paper equity {float(acct['equity']):,.2f} | "
        f"bot budget {config.BUDGET:,.0f} | ${config.BUDGET * config.RISK_PER_TRADE_PCT / 100:,.0f} risk per trade")
    return acct


def new_day_state():
    return {"avg_vol": None, "candidates": None, "broke": set(), "seen_bar": {}, "decisions": []}


def main():
    single_instance()
    keep_awake()
    startup_checks()
    stamp = code_stamp()
    news_mon, risk = NewsMonitor(), RiskManager()
    alerts, state, current_day = [], new_day_state(), None

    while True:
        try:
            restart_if_updated(stamp)
            if MAX_RUNTIME_MIN and time.time() - START_TIME > MAX_RUNTIME_MIN * 60:
                log(f"Reached the {MAX_RUNTIME_MIN:.0f}-minute run limit. Handing off to the next scheduled run.")
                return
            flag = os.path.join(LOG_DIR, "stop.flag")
            if os.path.exists(flag):
                os.remove(flag)
                log("Stop requested (stop_bot.bat). Shutting down. Open positions keep their stop and target orders.")
                return
            clk = api.clock()
            day, hhmm, offset = parse_clock(clk["timestamp"])
            now_min = to_min(hhmm)
            flatten_at, no_new_after = session_cutoffs(clk)
            acct = api.account()
            positions = api.positions()

            if day != current_day:  # new trading day: reset counters
                current_day = day
                risk = RiskManager()
                state = new_day_state()
                open_iso = f"{day}T09:30:00{offset}"
                buys = [o for o in api.todays_closed_orders(open_iso)
                        if o["side"] == "buy" and o.get("filled_at")]
                risk.trades_today = len(buys)
            decisions = state["decisions"]

            if not clk["is_open"]:
                status = f"Market closed. Next open: {clk['next_open'][:16].replace('T', ' ')} ET"
                dashboard.write(status, acct, positions, decisions, alerts, config.BUDGET)
                log(status)
                if AUTO and clk["next_open"][:10] != day:
                    log("No more trading today. Shutting down until the next scheduled start.")
                    return
                time.sleep(min(300, max(30, _secs_until(clk["timestamp"], clk["next_open"]))))
                continue

            open_iso = f"{day}T09:30:00{offset}"

            # End of day: flatten
            if hhmm >= flatten_at:
                if positions or api.open_orders():
                    log("Risk manager: closing all positions before the bell.")
                    api.flatten_all()
                status = f"{hhmm} ET - done for the day (flattened at {flatten_at})"
                dashboard.write(status, acct, api.positions(), decisions, alerts, config.BUDGET)
                time.sleep(config.LOOP_SECONDS)
                continue

            # Agent 2: news alerts
            since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
            articles = []
            for i in range(0, len(config.WATCHLIST), 20):
                articles += api.news(config.WATCHLIST[i:i + 20], since)
            for a in news_mon.new_alerts(articles):
                alerts.append(a)
                tag = "+" if a["sentiment"] > 0 else "-" if a["sentiment"] < 0 else " "
                log(f"NEWS [{tag}] {','.join(a['symbols'][:3])}: {a['headline'][:100]}")
            alerts = alerts[-200:]

            # Agent 1: chart scanner
            if state["avg_vol"] is None:
                state["avg_vol"] = avg_opening_volume(day)
                log(f"Scanner: loaded 10-day opening volume for {len(state['avg_vol'])} stocks.")
            today = completed_today(api.bars(config.WATCHLIST, open_iso, timeframe="5Min"), now_min)

            if state["candidates"] is None and now_min >= OPEN_MIN + 5:
                opening = {s: bs[0] for s, bs in today.items()}
                if opening:
                    state["candidates"] = scanner.pick_candidates(opening, state["avg_vol"])
                    names = ", ".join(f"{s} {r:.1f}x" for r, s in state["candidates"]) or "none (no stock hit 2.5x)"
                    log(f"Scanner: today's stocks in play: {names}")

            cands = state["candidates"] or []
            mkt_ok, mkt_why = scanner.market_ok(today.get("SPY", []))
            status = (f"{hhmm} ET | in play: {', '.join(s for _, s in cands) or 'none'} | "
                      f"market filter {'ON' if mkt_ok else 'off'} ({mkt_why}) | "
                      f"trades today {risk.trades_today}/{config.MAX_TRADES_PER_DAY}"
                      + (" | HALTED: daily loss limit" if risk.halted else ""))

            open_orders = api.open_orders()
            if mkt_ok:
                for rvol, sym in cands:
                    bars = today.get(sym, [])
                    if len(bars) < 2 or sym in state["broke"]:
                        continue
                    last_start = bars[-1]["t"]
                    if state["seen_bar"].get(sym) == last_start:
                        continue
                    if sym not in state["seen_bar"] and earlier_breakout(bars, today.get("SPY", [])):
                        # started mid-day: the first breakout already happened before we were watching
                        state["seen_bar"][sym] = last_start
                        state["broke"].add(sym)
                        log(f"{sym}: first breakout already happened earlier today, not chasing it.")
                        continue
                    state["seen_bar"][sym] = last_start
                    above, sig = scanner.check_breakout(sym, bars, rvol)
                    if not above:
                        continue
                    state["broke"].add(sym)  # only the first breakout counts
                    d = {"time": hhmm, "symbol": sym, "action": "SKIP"}
                    if not sig:
                        d["scanner"] = "broke out but below VWAP or stop too tight"
                        decisions.append(d); _save(d); log(f"{sym}: breakout skipped ({d['scanner']})")
                        continue
                    d["scanner"] = f"{sig['reason']} | entry {sig['entry']} stop {sig['stop']} target {sig['target']}"

                    # Agent 2 verdict
                    verdict, why = news_mon.verdict(sym, articles)
                    d["news"] = why
                    if verdict == "block":
                        decisions.append(d); _save(d); log(f"{sym}: blocked by news - {why}")
                        continue

                    # Agent 3: time machine (reports odds only)
                    try:
                        hist_start = (dt.date.fromisoformat(day) - dt.timedelta(days=7)).isoformat() + "T00:00:00Z"
                        hist = api.bars([sym], hist_start).get(sym, [])
                        sim = time_machine.simulate(sig["entry"], sig["stop"], sig["target"],
                                                    time_machine.minute_returns(hist),
                                                    to_min(flatten_at) - now_min)
                        d["time_machine"] = (f"{sim['p_target']:.0%} target / {sim['p_stop']:.0%} stop / "
                                             f"{sim['p_neither']:.0%} open at close (info only)") if sim else "not enough data"
                    except api.AlpacaError as e:
                        d["time_machine"] = f"skipped ({str(e)[:60]})"

                    # Agent 4: risk manager (final say)
                    ok, qty, why = risk.review(sig, acct, positions, open_orders, hhmm, no_new_after)
                    d["risk"] = why
                    if not ok:
                        decisions.append(d); _save(d); log(f"{sym}: blocked by risk manager - {why}")
                        continue

                    order = api.bracket_buy(sym, qty, sig["stop"], sig["target"])
                    risk.trades_today += 1
                    positions, open_orders = api.positions(), api.open_orders()
                    d["action"] = "BUY"
                    d["order_id"] = order.get("id")
                    decisions.append(d); _save(d)
                    log(f"BUY {qty} {sym} @~{sig['entry']} stop {sig['stop']} target {sig['target']} | {why}")

            dashboard.write(status, acct, api.positions(), decisions, alerts, config.BUDGET)
            log(status)
        except (KeyboardInterrupt, SystemExit):
            raise
        except api.AlpacaError as e:
            log(f"Alpaca error: {e}")
        except Exception:
            log("Unexpected error:\n" + traceback.format_exc())
        time.sleep(config.LOOP_SECONDS)


def _secs_until(now_ts, later_ts):
    try:
        a = dt.datetime.fromisoformat(now_ts[:19] + now_ts[-6:])
        b = dt.datetime.fromisoformat(later_ts[:19] + later_ts[-6:])
        return int((b - a).total_seconds())
    except ValueError:
        return 300


def _save(d):
    with open(os.path.join(LOG_DIR, "decisions.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(d) + "\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("Stopped. Open positions (if any) are still protected by their stop and target orders.")
