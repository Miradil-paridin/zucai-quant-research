"""Final live audit of the Jingcai repricing-lag hypothesis.

Aligns every pre-cutoff Jingcai HAD snapshot with the latest sharp-book
consensus price known at that moment, then settles "first time EV > threshold"
flat bets on the official full-time result and reports ROI, standard error,
CLV and the drift of apparent edges toward close.

Usage (from the backend directory, with the local PostgreSQL running):
    .\\.venv\\Scripts\\python.exe ..\\scripts\\final_live_audit.py
"""

from __future__ import annotations

import json
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import connect  # noqa: E402
from app.jingcai import repricing_lag as R  # noqa: E402
from app.jingcai.matching import jingcai_kickoff_utc, resolve_consensus_event  # noqa: E402

OUT = ROOT / "output" / "final_live_audit"


def _outcome(ft_score: str | None) -> int | None:
    if not ft_score or ":" not in str(ft_score):
        return None
    try:
        home, away = (int(x) for x in str(ft_score).split(":")[:2])
    except ValueError:
        return None
    return 0 if home > away else 1 if home == away else 2


def collect_pairs() -> list[dict]:
    pairs: list[dict] = []
    with connect() as c:
        results = {
            str(r["official_match_id"]): _outcome(r["ft_score"])
            for r in c.execute("SELECT official_match_id, ft_score FROM jingcai.jc_result_history").fetchall()
        }
        trajectories = R._jc_trajectories(c)
        for row in c.execute("SELECT * FROM jingcai.jc_matches").fetchall():
            match = trajectories.get(int(row["jc_match_id"]))
            if not match:
                continue
            event = resolve_consensus_event(
                c, league_name=row["league_name"], home_team=row["home_team"],
                away_team=row["away_team"], match_date_local=row["match_date_local"],
                match_time_local=row["match_time_local"],
            )
            if not event:
                continue
            cutoff = R._sale_cutoff(match)
            kickoff = jingcai_kickoff_utc(match["match_date_local"], match["match_time_local"])
            if not cutoff or not kickoff:
                continue
            consensus = sorted(
                (x for x in R._consensus_trajectory(c, event.provider_event_id) if x["t"] < kickoff),
                key=lambda x: x["t"],
            )
            if not consensus:
                continue
            final = consensus[-1]
            closing_ok = 0 <= (kickoff - final["t"]).total_seconds() / 3600 <= R.MAX_ALIGN_GAP_HOURS
            for snap in sorted(match["snaps"], key=lambda x: x["t"]):
                if snap["t"] >= cutoff:
                    continue
                known = [x for x in consensus if x["t"] <= snap["t"]]
                if not known:
                    continue
                latest = known[-1]
                if (snap["t"] - latest["t"]).total_seconds() / 3600 > R.MAX_ALIGN_GAP_HOURS:
                    continue
                odds = (snap["h"], snap["d"], snap["a"])
                pairs.append({
                    "mid": int(row["jc_match_id"]), "league": row["league_name"],
                    "date": str(row["match_date_local"]), "t": snap["t"].isoformat(),
                    "odds": odds,
                    "p_now": R._devig(latest["h"], latest["d"], latest["a"]),
                    "p_final": R._devig(final["h"], final["d"], final["a"]),
                    "closing_ok": closing_ok,
                    "outcome": results.get(str(row["official_match_id"])),
                    "jc_margin": sum(1 / o for o in odds) - 1,
                })
    return pairs


def first_signals(pairs: list[dict], threshold: float) -> list[dict]:
    by_match: dict[int, list[dict]] = defaultdict(list)
    for p in pairs:
        by_match[p["mid"]].append(p)
    bets, seen = [], set()
    for mid, rows in by_match.items():
        for p in sorted(rows, key=lambda x: x["t"]):
            for i in range(3):
                edge = p["p_now"][i] * p["odds"][i] - 1
                if edge > threshold and (mid, i) not in seen:
                    seen.add((mid, i))
                    bets.append({
                        "mid": mid, "league": p["league"], "date": p["date"], "outcome": "HDA"[i],
                        "odd": p["odds"][i], "p": p["p_now"][i], "edge": edge,
                        "clv": p["p_final"][i] * p["odds"][i] - 1 if p["closing_ok"] else None,
                        "pnl": None if p["outcome"] is None else (p["odds"][i] - 1 if p["outcome"] == i else -1.0),
                    })
    return bets


def summarize(pairs: list[dict]) -> dict:
    edges = [p["p_now"][i] * p["odds"][i] - 1 for p in pairs for i in range(3)]
    report = {
        "pairs": len(pairs),
        "matches": len({p["mid"] for p in pairs}),
        "jc_margin_mean": round(st.mean(p["jc_margin"] for p in pairs), 4),
        "edge_mean": round(st.mean(edges), 4),
        "share_edge_gt_0": round(sum(e > 0 for e in edges) / len(edges), 4),
        "share_edge_gt_2pct": round(sum(e > 0.02 for e in edges) / len(edges), 4),
        "thresholds": {},
    }
    for th in (0.0, 0.01, 0.02, 0.03, 0.05):
        bets = first_signals(pairs, th)
        settled = [b for b in bets if b["pnl"] is not None]
        clvs = [b["clv"] for b in bets if b["clv"] is not None]
        if not settled:
            continue
        variance = sum(b["p"] * (b["odd"] - 1) ** 2 + (1 - b["p"]) - (b["p"] * b["odd"] - 1) ** 2 for b in settled)
        report["thresholds"][f"{th:.2f}"] = {
            "bets": len(settled), "won": sum(b["pnl"] > 0 for b in settled),
            "roi": round(sum(b["pnl"] for b in settled) / len(settled), 4),
            "roi_se": round(math.sqrt(variance) / len(settled), 4),
            "clv_mean": round(st.mean(clvs), 4) if clvs else None, "clv_n": len(clvs),
        }
    drift: dict[float, list[float]] = defaultdict(list)
    for p in pairs:
        if not p["closing_ok"]:
            continue
        for i in range(3):
            edge = p["p_now"][i] * p["odds"][i] - 1
            drift[round(math.floor(edge * 50) / 50, 2)].append((p["p_final"][i] - p["p_now"][i]) * p["odds"][i])
    report["edge_drift_to_close"] = {
        f"{k:+.2f}": {"n": len(v), "mean": round(st.mean(v), 4)} for k, v in sorted(drift.items()) if len(v) >= 5
    }
    report["signals_gt_2pct"] = sorted(first_signals(pairs, 0.02), key=lambda b: b["date"])
    return report


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pairs = collect_pairs()
    (OUT / "pairs.json").write_text(json.dumps(pairs, ensure_ascii=False), encoding="utf-8")
    report = summarize(pairs)
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "signals_gt_2pct"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
