from __future__ import annotations

import math
from datetime import datetime, timedelta
from statistics import median

VERSION = "market-median-v1"
CHECKPOINTS = {"h24": 24, "h6": 6, "h1": 1}
FRESHNESS = {"h24": 6, "h6": 3, "h1": 1, "manual": 3, "live": 3}
# Regional brand variants count once. Exchanges are excluded because the stored
# back odds do not account for their commission or available liquidity.
EXCLUDED = {"betfair_ex_eu", "matchbook"}
FAMILIES = {"unibet_fr": "unibet", "unibet_se": "unibet", "unibet_nl": "unibet",
            "winamax_fr": "winamax", "winamax_de": "winamax", "betsson": "betsson",
            "nordicbet": "betsson"}


def due_checkpoint(kickoff: datetime, now: datetime) -> str | None:
    hours = (kickoff - now).total_seconds() / 3600
    return next((name for name, target in CHECKPOINTS.items() if target - .5 < hours <= target), None)


def probabilities(odds) -> list[float] | None:
    if len(odds) != 3 or any(x is None or not math.isfinite(float(x)) or float(x) <= 1 for x in odds):
        return None
    inverses = [1 / float(x) for x in odds]
    return [x / sum(inverses) for x in inverses]


def market_reference(quotes: list[dict], now: datetime, kickoff: datetime, phase: str = "live") -> dict:
    families = {}
    for quote in quotes:
        observed = quote["observed_at_utc"]
        if quote["bookmaker"] in EXCLUDED or observed > now or observed >= kickoff:
            continue
        if now - observed > timedelta(hours=FRESHNESS[phase]):
            continue
        p = probabilities([quote.get("odd_home"), quote.get("odd_draw"), quote.get("odd_away")])
        if p is None:
            continue
        family = FAMILIES.get(quote["bookmaker"], quote["bookmaker"])
        candidate = {**quote, "probabilities": p, "family": family}
        previous = families.get(family)
        if previous is None or (observed, quote["snapshot_id"]) > (previous["observed_at_utc"], previous["snapshot_id"]):
            families[family] = candidate
    selected = sorted(families.values(), key=lambda x: x["family"])
    if not selected:
        return {"status": "missing_fresh_odds", "probabilities": None, "sources": [], "source_count": 0, "dispersion": None}
    medians = [median(q["probabilities"][i] for q in selected) for i in range(3)]
    p = [x / sum(medians) for x in medians]
    dispersion = max(max(q["probabilities"][i] for q in selected)-min(q["probabilities"][i] for q in selected) for i in range(3))
    status = "ready" if len(selected) >= 3 and dispersion <= .15 else "insufficient_sources" if len(selected) < 3 else "market_disagreement"
    return {"status": status, "probabilities": p, "sources": selected,
            "source_count": len(selected), "dispersion": dispersion}


def score_probability(p: list[float], outcome: str) -> dict:
    target = {"H": 0, "D": 1, "A": 2}[outcome]
    return {"correct": max(range(3), key=lambda i: p[i]) == target,
            "brier": sum((value - (i == target)) ** 2 for i, value in enumerate(p)) / 3,
            "log_loss": -math.log(max(p[target], 1e-12))}


def summarize(rows: list[dict]) -> dict:
    settled = [r for r in rows if r.get("outcome") in {"H", "D", "A"}]
    scored = [score_probability(r["probabilities"], r["outcome"]) for r in settled]
    n = len(scored)
    correct = sum(s["correct"] for s in scored)
    interval = None
    if n:
        p = correct / n
        z = 1.96
        center = (p + z*z/(2*n)) / (1+z*z/n)
        margin = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1+z*z/n)
        interval = [max(0, center-margin), min(1, center+margin)]
    bins = []
    for index in range(10):
        values = [(p, int(i == {"H": 0, "D": 1, "A": 2}[r["outcome"]]))
                  for r in settled for i, p in enumerate(r["probabilities"])
                  if min(9, int(p*10)) == index]
        bins.append({"from": index/10, "to": (index+1)/10, "count": len(values),
                     "predicted": sum(x[0] for x in values)/len(values) if values else None,
                     "observed": sum(x[1] for x in values)/len(values) if values else None})
    return {"saved": len(rows), "settled": n, "pending": sum(r.get("outcome") is None for r in rows),
            "void": sum(r.get("outcome") == "void" for r in rows), "correct": correct,
            "accuracy": correct/n if n else None, "accuracy_interval_95": interval,
            "brier": sum(s["brier"] for s in scored)/n if n else None,
            "log_loss": sum(s["log_loss"] for s in scored)/n if n else None,
            "calibration": bins, "evidence": "collecting" if n < 500 else "review_required"}
