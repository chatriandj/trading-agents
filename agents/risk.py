"""Agent 4 - Risk Manager. Has the final say on every trade."""
import math

import config


class RiskManager:
    def __init__(self):
        self.trades_today = 0
        self.halted = False

    def daily_pnl(self, acct):
        return float(acct["equity"]) - float(acct["last_equity"])

    def check_halt(self, acct):
        if self.daily_pnl(acct) <= -config.BUDGET * config.DAILY_LOSS_LIMIT_PCT / 100:
            self.halted = True
        return self.halted

    def size(self, entry, stop):
        risk_dollars = config.BUDGET * config.RISK_PER_TRADE_PCT / 100
        per_share = entry - stop
        qty = math.floor(risk_dollars / per_share) if per_share > 0 else 0
        max_qty = math.floor(config.BUDGET * config.MAX_POSITION_PCT / 100 / entry)
        return max(0, min(qty, max_qty))

    def review(self, signal, acct, positions, open_orders, et_hhmm, no_new_after=None):
        """Returns (approved, qty, reason)."""
        no_new_after = no_new_after or config.NO_NEW_TRADES_AFTER
        if self.halted or self.check_halt(acct):
            return False, 0, f"daily loss limit hit ({config.DAILY_LOSS_LIMIT_PCT}% of budget)"
        if et_hhmm >= no_new_after:
            return False, 0, f"no new trades after {no_new_after} ET"
        if self.trades_today >= config.MAX_TRADES_PER_DAY:
            return False, 0, f"max {config.MAX_TRADES_PER_DAY} trades per day reached"
        held = {p["symbol"] for p in positions} | {o["symbol"] for o in open_orders}
        if signal["symbol"] in held:
            return False, 0, "already in this stock"
        if len({p["symbol"] for p in positions}) >= config.MAX_OPEN_POSITIONS:
            return False, 0, f"max {config.MAX_OPEN_POSITIONS} open positions"
        qty = self.size(signal["entry"], signal["stop"])
        if qty < 1:
            return False, 0, "position would be under 1 share"
        risk = qty * (signal["entry"] - signal["stop"])
        return True, qty, f"{qty} sh, risking ${risk:.2f} ({risk / config.BUDGET:.2%} of budget)"
