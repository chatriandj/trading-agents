"""Settings for the trading agents. Edit the numbers in the SETTINGS section to tune the bot."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_env(path):
    """Tiny .env reader so no extra packages are needed."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(os.path.join(HERE, "keys.txt"))
_load_env(os.path.join(HERE, ".env"))

API_KEY = os.environ.get("APCA_API_KEY_ID", "").strip()  # .strip(): pasted keys often carry a stray newline
API_SECRET = os.environ.get("APCA_API_SECRET_KEY", "").strip()
BASE_URL = os.environ.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
DATA_URL = "https://data.alpaca.markets"
DATA_FEED = os.environ.get("DATA_FEED", "iex")  # free plan = iex
ALLOW_LIVE = os.environ.get("ALLOW_LIVE", "no").lower() == "yes"

# ---------------- SETTINGS ----------------
# Stocks the chart scanner watches (liquid names that move during the day).
WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "AMZN", "GOOGL", "NFLX", "AVGO",
    "PLTR", "SMCI", "MU", "INTC", "COIN", "SOFI", "UBER", "SHOP", "CRM", "ORCL",
    "BAC", "JPM", "XOM", "CVX", "BA", "DIS", "NKE", "WMT", "COST", "PYPL",
    "SPY", "QQQ", "IWM", "MARA", "RIVN", "LCID", "F", "GM", "SNAP", "HOOD",
]

BUDGET = float(os.environ.get("BUDGET", "10000"))  # pretend account size the bot sizes trades from
RISK_PER_TRADE_PCT = 0.5        # % of budget lost if a trade hits its stop ($50 on $10k)
MAX_POSITION_PCT = 100          # max % of budget in one stock
MAX_OPEN_POSITIONS = 2
MAX_TRADES_PER_DAY = 2
DAILY_LOSS_LIMIT_PCT = 2.0      # stop trading for the day after losing this % of budget

NO_NEW_TRADES_AFTER = "15:30"   # ET
FLATTEN_AT = "15:50"            # ET, close everything before the bell

# ---- Version 2 strategy (passed the backtest on unseen months) ----
RVOL_MIN = 2.5                  # opening 5-min volume must be >= 2.5x its 10-day average
TOP_CANDIDATES = 5              # watch only the 5 most "in play" stocks each day
REWARD_RISK = 3.0               # target = entry + 3 x risk (stop = opening 5-min low)

# ---- Version 1 settings (only used by backtest.py to reproduce the old results) ----
OPENING_RANGE_MIN = 15
STOP_ATR_MULT = 1.5
V1_REWARD_RISK = 2.0

# Time machine: now reports odds for each idea but no longer blocks trades
# (in the backtest it approved almost everything, so it added nothing).
SIMULATIONS = 10_000
MIN_PROB_TARGET = 0.25
MAX_PROB_NEITHER = 0.30

LOOP_SECONDS = 30
