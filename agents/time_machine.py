"""Agent 3 - Time Machine.
Replays the rest of today 10,000 times using real recent 1-minute moves (bootstrap)
and measures how often the target is hit before the stop.
"""
import math
import random

import config


def minute_returns(history_bars):
    rets = []
    for i in range(1, len(history_bars)):
        p0, p1 = history_bars[i - 1]["c"], history_bars[i]["c"]
        if p0 > 0 and p1 > 0:
            r = math.log(p1 / p0)
            if abs(r) < 0.08:  # skip overnight gaps / bad prints
                rets.append(r)
    return rets


def simulate(entry, stop, target, returns, minutes_left, n=None, seed=None):
    n = n or config.SIMULATIONS
    rng = random.Random(seed)
    if len(returns) < 100 or minutes_left < 5:
        return None
    mean = sum(returns) / len(returns)
    centered = [r - mean for r in returns]  # remove drift: no free edge from past trend
    ls, lt = math.log(stop / entry), math.log(target / entry)
    hit_t = hit_s = 0
    closes, results_r = [], []
    risk = entry - stop
    choice = rng.choice
    for _ in range(n):
        x, outcome = 0.0, None
        for _ in range(minutes_left):
            x += choice(centered)
            if x <= ls:
                outcome = "stop"
                break
            if x >= lt:
                outcome = "target"
                break
        price = entry * math.exp(x)
        if outcome == "target":
            hit_t += 1
            results_r.append(config.REWARD_RISK)
        elif outcome == "stop":
            hit_s += 1
            results_r.append(-1.0)
        else:
            results_r.append((price - entry) / risk)
        closes.append(price)
    closes.sort()
    pct = lambda q: closes[min(len(closes) - 1, int(q * len(closes)))]
    return {
        "sims": n, "minutes_left": minutes_left,
        "p_target": round(hit_t / n, 3), "p_stop": round(hit_s / n, 3),
        "p_neither": round((n - hit_t - hit_s) / n, 3),
        "expected_r": round(sum(results_r) / n, 3),
        "close_p5": round(pct(0.05), 2), "close_p50": round(pct(0.5), 2), "close_p95": round(pct(0.95), 2),
    }


def approve(result):
    if not result:
        return False, "not enough data to simulate"
    ok = result["p_target"] >= config.MIN_PROB_TARGET and result["p_neither"] <= config.MAX_PROB_NEITHER
    why = (f"{result['p_target']:.0%} target / {result['p_stop']:.0%} stop, "
           f"{result['p_neither']:.0%} unresolved, {result['sims']:,} sims")
    return ok, why
