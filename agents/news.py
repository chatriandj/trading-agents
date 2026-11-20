"""Agent 2 - News Monitor.
Alerts on new headlines for the watchlist and vetoes trades on bad news.
"""
NEGATIVE = [
    "downgrade", "lawsuit", "sues", "investigation", "probe", "recall", "misses", "miss estimates",
    "cuts guidance", "lowers guidance", "offering", "dilution", "bankruptcy", "halt", "plunge",
    "fraud", "subpoena", "layoffs", "warning", "delist", "short report", "resigns",
]
POSITIVE = [
    "upgrade", "beats", "beat estimates", "raises guidance", "record", "approval", "approved",
    "partnership", "contract", "buyback", "acquire", "surge", "outperform",
]


def score_headline(text):
    t = text.lower()
    neg = sum(w in t for w in NEGATIVE)
    pos = sum(w in t for w in POSITIVE)
    return pos - neg


class NewsMonitor:
    def __init__(self):
        self.seen = set()

    def new_alerts(self, articles):
        """Return headlines we haven't shown before."""
        alerts = []
        for a in articles:
            if a["id"] in self.seen:
                continue
            self.seen.add(a["id"])
            alerts.append({
                "symbols": a.get("symbols", []), "headline": a.get("headline", ""),
                "source": a.get("source", ""), "time": a.get("created_at", ""),
                "sentiment": score_headline(a.get("headline", "") + " " + a.get("summary", "")),
            })
        return alerts

    @staticmethod
    def verdict(symbol, articles):
        """'block', 'boost' or 'neutral' for a trade idea based on recent headlines."""
        mine = [a for a in articles if symbol in a.get("symbols", [])]
        if not mine:
            return "neutral", "no recent news"
        scores = [(score_headline(a["headline"] + " " + a.get("summary", "")), a["headline"]) for a in mine]
        worst = min(scores)
        if worst[0] < 0:
            return "block", f"bad news: {worst[1][:90]}"
        best = max(scores)
        if best[0] > 0:
            return "boost", f"catalyst: {best[1][:90]}"
        return "neutral", f"{len(mine)} headline(s), nothing alarming"
