"""Agent 1 - Chart Scanner (version 2: stocks in play).

Rules (the ones that passed the backtest on unseen months):
  1. Stocks in play: the first 5-minute bar (9:30-9:35 ET) traded at least
     RVOL_MIN x the stock's own average first-5-minute volume (last 10 days).
     Only the top TOP_CANDIDATES by that ratio are watched each day.
  2. Market filter: SPY is above its open and above its VWAP.
  3. Signal: the first completed 5-minute bar (seen while the market filter
     is on) that closes above the opening 5-minute high, while above VWAP.
  4. Stop = low of the opening 5 minutes. Target = 3 x risk.
"""
import config

ETFS = {"SPY", "QQQ", "IWM"}


def vwap(bars):
    pv = sum(((b["h"] + b["l"] + b["c"]) / 3) * b["v"] for b in bars)
    vol = sum(b["v"] for b in bars)
    return pv / vol if vol else bars[-1]["c"]


def pick_candidates(opening_bars, avg_open_vol):
    """opening_bars: {sym: first 5-min bar today}; avg_open_vol: {sym: 10-day avg}.
    Returns [(rvol, sym), ...] best first."""
    out = []
    for sym, b in opening_bars.items():
        avg = avg_open_vol.get(sym)
        if sym in ETFS or not avg or b["o"] < 5:
            continue
        rvol = b["v"] / avg
        if rvol >= config.RVOL_MIN:
            out.append((round(rvol, 2), sym))
    return sorted(out, reverse=True)[:config.TOP_CANDIDATES]


def market_ok(spy_bars):
    """SPY above its open and its VWAP (completed 5-min bars)."""
    if not spy_bars:
        return False, "no SPY data"
    last = spy_bars[-1]["c"]
    vw, opn = vwap(spy_bars), spy_bars[0]["o"]
    ok = last > opn and last > vw
    return ok, f"SPY {last:.2f} vs open {opn:.2f} / VWAP {vw:.2f}"


def check_breakout(symbol, bars, rvol):
    """bars: today's COMPLETED 5-min bars. Looks at the latest bar.
    Returns (closed_above_opening_high, signal_or_None). The caller remembers the
    first close above the high so only the first breakout is ever traded."""
    if len(bars) < 2:
        return False, None
    or_high, or_low = bars[0]["h"], bars[0]["l"]
    price = bars[-1]["c"]
    if price <= or_high:
        return False, None
    vw = vwap(bars)
    if price <= vw:
        return True, None
    stop = or_low
    risk = price - stop
    if risk < price * 0.0015:
        return True, None
    return True, {
        "symbol": symbol, "entry": round(price, 2), "stop": round(stop, 2),
        "target": round(price + config.REWARD_RISK * risk, 2), "rvol": rvol,
        "or_high": round(or_high, 2), "vwap": round(vw, 2), "score": rvol,
        "reason": f"In play ({rvol:.1f}x opening volume), first 5-min close above {or_high:.2f}, above VWAP {vw:.2f}",
    }
