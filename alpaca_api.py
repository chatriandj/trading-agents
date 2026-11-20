"""Minimal Alpaca REST client (standard library only)."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import config


class AlpacaError(Exception):
    pass


def _request(method, url, params=None, body=None, retries=3):
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "APCA-API-KEY-ID": config.API_KEY,
        "APCA-API-SECRET-KEY": config.API_SECRET,
        "Content-Type": "application/json",
    }
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")
            if e.code == 429 and attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            raise AlpacaError(f"{method} {url.split('?')[0]} -> {e.code}: {msg}")
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            raise AlpacaError(f"Network error: {e}")


def trade(method, path, **kw):
    return _request(method, config.BASE_URL + path, **kw)


def data(path, params):
    return _request("GET", config.DATA_URL + path, params=params)


# ---- trading ----
def account():
    return trade("GET", "/v2/account")


def clock():
    return trade("GET", "/v2/clock")


def positions():
    return trade("GET", "/v2/positions") or []


def open_orders():
    return trade("GET", "/v2/orders", params={"status": "open", "limit": 500}) or []


def todays_closed_orders(after_iso):
    return trade("GET", "/v2/orders", params={"status": "closed", "after": after_iso, "limit": 500}) or []


def bracket_buy(symbol, qty, stop, target):
    return trade("POST", "/v2/orders", body={
        "symbol": symbol, "qty": str(qty), "side": "buy", "type": "market",
        "time_in_force": "day", "order_class": "bracket",
        "take_profit": {"limit_price": f"{target:.2f}"},
        "stop_loss": {"stop_price": f"{stop:.2f}"},
    })


def flatten_all():
    trade("DELETE", "/v2/orders")
    trade("DELETE", "/v2/positions", params={"cancel_orders": "true"})


# ---- market data ----
def bars(symbols, start_iso, timeframe="1Min"):
    """Returns {symbol: [bar, ...]} with pagination."""
    out, token = {}, None
    while True:
        r = data("/v2/stocks/bars", {
            "symbols": ",".join(symbols), "timeframe": timeframe, "start": start_iso,
            "feed": config.DATA_FEED, "limit": 10000, "adjustment": "raw",
            "page_token": token,
        })
        for sym, bs in (r.get("bars") or {}).items():
            out.setdefault(sym, []).extend(bs)
        token = r.get("next_page_token")
        if not token:
            return out


def news(symbols, start_iso, limit=50):
    r = data("/v1beta1/news", {"symbols": ",".join(symbols), "start": start_iso,
                               "limit": limit, "sort": "desc"})
    return r.get("news") or []
