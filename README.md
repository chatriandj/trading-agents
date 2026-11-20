# Trading Agents

**Built by Jackson Chatriand** · Central Washington University, Business Administration (Marketing Management) + Business Analytics

A multi-agent day-trading bot that paper trades US stocks through the [Alpaca](https://alpaca.markets) API. Four agents each check every trade idea, and any of them can stop it. It runs automatically every weekday on GitHub Actions.

**Live dashboard:** https://chatriandj.github.io/trading-agents/

> Paper trading only (simulated money). Educational project, not financial advice.

## The four agents

| Agent | Job |
|---|---|
| **Chart scanner** (`agents/scanner.py`) | At 9:35 AM ET picks the day's "stocks in play" (opening 5-minute volume at least 2.5x their 10-day average), then waits for the first 5-minute close above the opening range high, above VWAP, while SPY is up on the day. |
| **News monitor** (`agents/news.py`) | Streams headlines for the watchlist and blocks a trade on bad news (downgrades, lawsuits, offerings, probes). |
| **Time machine** (`agents/time_machine.py`) | Monte Carlo: replays the rest of the day 10,000 times from real recent price moves and reports the odds of hitting the target before the stop. |
| **Risk manager** (`agents/risk.py`) | Final say: $50 risk per trade (0.5% of a $10,000 budget), max 2 trades a day, daily loss limit, no new trades after 3:30 PM ET, everything closed by 3:50 PM ET. |

Every trade is a bracket order (stop = low of the opening 5 minutes, target = 3x the risk).

## How the strategy was chosen

I backtested on 12 months of 1-minute market data (Dec 2025 to Nov 2026, 40 liquid stocks, slippage included).

| Version | Test | Trades | Win rate | P&L | Profit factor |
|---|---|---|---|---|---|
| v1: 1-minute breakouts, tight stops | Full year | 1,450 | 32% | -$2,275 | 0.76 |
| v2: stocks in play, 5-minute bars | Tuning months (Dec-Jul) | 84 | 60% | +$534 | 1.52 |
| **v2** | **Unseen months (Aug-Nov)** | **37** | **54%** | **+$218** | **1.64** |

Version 1 lost money mostly to trading costs. Version 2 was tuned only on December-July, then tested once on August-November, which it had never seen. It stayed profitable, but 37 trades is a small sample, so it is being paper traded before any real money is considered.

Full report: [backtest v2](https://chatriandj.github.io/trading-agents/backtest-v2.html)

## Running it

- **Automatically:** `.github/workflows/trading-bot.yml` runs the bot twice each weekday (GitHub limits each run to 6 hours) and saves the log and dashboard back to this repo. Alpaca keys are stored as encrypted repository secrets (`APCA_API_KEY_ID`, `APCA_API_SECRET_KEY`).
- **Locally:** set the same two environment variables (or put them in a `keys.txt` file next to `main.py`) and run `python main.py`. No packages to install.
- **Backtests:** `python backtest.py` downloads a year of data and tests v1; `python backtest_v2.py` tunes and tests v2.

## Files

- `main.py`: runs the agents together
- `agents/`: the four agents
- `config.py`: watchlist, budget and risk settings
- `alpaca_api.py`: small Alpaca client (standard library only)
- `dashboard.py`: writes the live dashboard page
- `backtest.py`, `backtest_v2.py`: backtests
- `logs/`: the bot's daily log and every trade decision

---

© 2026 Jackson Chatriand. All rights reserved. See [LICENSE](LICENSE).
