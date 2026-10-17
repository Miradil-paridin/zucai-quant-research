"""竞彩调价滞后分析: 实时快照双侧对齐 + 信号识别 + CLV 结算。

对每场可关联比赛:
- 竞彩侧: jc_odds_snapshots(had) 的价格轨迹;
- 共识侧: consensus_odds_snapshots 中锐利 bookmaker 的价格轨迹;
- 在每个竞彩快照时刻, 取"当时已知"的最近共识价, 计算
  edge = p_consensus * jc_odd - 1; edge > 阈值 记为一次滞后信号;
- 信号用"停售前最后一次共识价"(≈封盘)结算 CLV:
  clv = p_final * jc_odd - 1。CLV 稳定为正 = 竞彩确有调价滞后错价。

这是研究诊断, 不构成投注建议。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from ..database import connect
from .matching import jingcai_kickoff_utc, resolve_consensus_event

SHARP_BOOKMAKERS = ("pinnacle", "betfair_ex_eu", "marathonbet", "onexbet")
SIGNAL_EDGE_THRESHOLD = 0.02
MAX_ALIGN_GAP_HOURS = 6.0


def _devig(h: float, d: float, a: float) -> tuple[float, float, float]:
    inv = (1 / h, 1 / d, 1 / a)
    total = sum(inv)
    return inv[0] / total, inv[1] / total, inv[2] / total


def _jc_trajectories(connection: Any) -> dict[int, dict[str, Any]]:
    matches: dict[int, dict[str, Any]] = {}
    rows = connection.execute(
        """
        SELECT s.jc_match_id, m.match_num_str, m.league_name, m.home_team,
               m.away_team, m.match_date_local, s.odd_home::float8 AS h,
               s.odd_draw::float8 AS d, s.odd_away::float8 AS a,
               m.match_time_local, m.sell_end_local,
               observed.t AS observed_at_utc
        FROM jingcai.jc_odds_snapshots s
        JOIN jingcai.jc_matches m USING (jc_match_id)
        JOIN LATERAL (SELECT DISTINCT t FROM unnest(
          ARRAY[s.captured_at_utc, s.last_observed_at_utc] || s.observation_times_utc) points(t)) observed ON true
        WHERE s.pool_code = 'had' AND s.point_in_time_safe
          AND s.odd_home > 1 AND s.odd_draw > 1 AND s.odd_away > 1
        ORDER BY s.jc_match_id, observed_at_utc
        """
    ).fetchall()
    for row in rows:
        entry = matches.setdefault(
            int(row["jc_match_id"]),
            {
                "match_num": row["match_num_str"],
                "league": row["league_name"],
                "home": row["home_team"],
                "away": row["away_team"],
                "date": str(row["match_date_local"]),
                "match_date_local": row["match_date_local"],
                "match_time_local": row["match_time_local"],
                "sell_end_local": row["sell_end_local"],
                "snaps": [],
            },
        )
        entry["snaps"].append(
            {"h": row["h"], "d": row["d"], "a": row["a"], "t": row["observed_at_utc"]}
        )
    return matches


def _consensus_trajectory(connection: Any, provider_event_id: str) -> list[dict[str, Any]]:
    for bookmaker in SHARP_BOOKMAKERS:
        rows = connection.execute(
            """
            SELECT odd_home::float8 AS h, odd_draw::float8 AS d,
                   odd_away::float8 AS a,
                   observed.t AS t,
                   commence_time_utc AS kickoff
            FROM jingcai.consensus_odds_snapshots s
            JOIN LATERAL (SELECT DISTINCT t FROM unnest(
              ARRAY[s.captured_at_utc, s.last_observed_at_utc] || s.observation_times_utc) points(t)) observed ON true
            WHERE provider_event_id = %s AND bookmaker = %s
              AND point_in_time_safe AND market_type = '1X2'
              AND odd_home > 1 AND odd_draw > 1 AND odd_away > 1
              AND observed.t < commence_time_utc
            ORDER BY t
            """,
            (provider_event_id, bookmaker),
        ).fetchall()
        if rows:
            return [dict(row) for row in rows]
    return []


def _sale_cutoff(match: dict[str, Any]) -> datetime | None:
    kickoff = jingcai_kickoff_utc(match["match_date_local"], match["match_time_local"])
    raw = match.get("sell_end_local")
    if raw:
        try:
            end = datetime.fromisoformat(raw)
            from zoneinfo import ZoneInfo
            end = end.replace(tzinfo=ZoneInfo("Asia/Shanghai")) if end.tzinfo is None else end
            return min(kickoff, end.astimezone(UTC)) if kickoff else end.astimezone(UTC)
        except ValueError:
            pass
    return kickoff


def evaluate_match(match: dict[str, Any], consensus: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    cutoff = _sale_cutoff(match)
    kickoff = jingcai_kickoff_utc(match["match_date_local"], match["match_time_local"])
    pairs = 0
    signals = []
    seen = set()
    if cutoff is None or kickoff is None:
        return {"pairs": 0, "signals": [], "status": "missing_kickoff"}
    consensus = sorted((x for x in consensus if x["t"] < kickoff and x["t"] <= now), key=lambda x: x["t"])
    if not consensus:
        return {"pairs": 0, "signals": [], "status": "no_prematch_consensus"}
    final = consensus[-1]
    closing_age = (kickoff - final["t"]).total_seconds() / 3600
    closing_available = now >= kickoff and 0 <= closing_age <= MAX_ALIGN_GAP_HOURS
    for snap in sorted(match["snaps"], key=lambda x: x["t"]):
        if snap["t"] >= cutoff or snap["t"] > now:
            continue
        previous = [x for x in consensus if x["t"] <= snap["t"]]
        if not previous:
            continue
        latest = previous[-1]
        gap = (snap["t"] - latest["t"]).total_seconds() / 3600
        if gap > MAX_ALIGN_GAP_HOURS:
            continue
        pairs += 1
        p_now = _devig(latest["h"], latest["d"], latest["a"])
        p_final = _devig(final["h"], final["d"], final["a"])
        for i, (outcome, odd) in enumerate(zip(("home", "draw", "away"), (snap["h"], snap["d"], snap["a"]))):
            edge = p_now[i] * odd - 1
            if edge <= SIGNAL_EDGE_THRESHOLD or outcome in seen:
                continue
            seen.add(outcome)
            signals.append({
                "match": f"{match['match_num']} {match['home']} vs {match['away']}",
                "league": match["league"], "date": match["date"], "outcome": outcome,
                "jc_odd": odd, "edge_at_signal": round(edge, 4),
                "signal_at_utc": snap["t"].isoformat(),
                "consensus_at_utc": latest["t"].isoformat(),
                "align_gap_hours": round(gap, 2), "closing_age_hours": round(closing_age, 2),
                "clv_vs_final": round(p_final[i] * odd - 1, 4) if closing_available else None,
                "evaluation_status": "closing_proxy_available" if closing_available
                    else "pending_kickoff" if now < kickoff else "missing_fresh_closing_proxy",
            })
    return {"pairs": pairs, "signals": signals, "status": "aligned" if pairs else "no_aligned_prematch_snapshots"}


def analyze_repricing_lag() -> dict[str, Any]:
    signals: list[dict[str, Any]] = []
    matched = unmatched = aligned_pairs = 0
    match_methods: dict[str, int] = defaultdict(int)
    coverage: list[dict[str, Any]] = []
    now_utc = datetime.now(UTC)
    with connect() as connection:
        trajectories = _jc_trajectories(connection)
        all_matches = connection.execute("SELECT * FROM jingcai.jc_matches ORDER BY match_date_local, jc_match_id").fetchall()
        for row in all_matches:
            jc_match = trajectories.get(int(row["jc_match_id"]))
            diagnostic = {"jc_match_id": int(row["jc_match_id"]), "league": row["league_name"],
                          "date": str(row["match_date_local"]), "home": row["home_team"], "away": row["away_team"],
                          "provider_event_id": None, "aligned_pairs": 0}
            coverage.append(diagnostic)
            # Identity linkage is reported even when the HAD market was never offered.
            identity = dict(league_name=row["league_name"], home_team=row["home_team"],
                            away_team=row["away_team"], match_date_local=row["match_date_local"],
                            match_time_local=row["match_time_local"])
            event = resolve_consensus_event(
                connection, **identity,
            )
            if event:
                diagnostic.update(provider_event_id=event.provider_event_id, match_method=event.method)
            if not jc_match:
                diagnostic["status"] = "no_had_snapshots"
                continue
            consensus = (
                _consensus_trajectory(connection, event.provider_event_id)
                if event is not None
                else []
            )
            if not consensus:
                unmatched += 1
                diagnostic["status"] = "unmatched_event" if event is None else "no_prematch_consensus"
                continue
            matched += 1
            match_methods[event.method] += 1
            evaluated = evaluate_match(jc_match, consensus, now_utc)
            aligned_pairs += evaluated["pairs"]
            signals.extend(evaluated["signals"])
            diagnostic.update(status=evaluated["status"], aligned_pairs=evaluated["pairs"], signals=len(evaluated["signals"]))
    clvs = [
        signal["clv_vs_final"]
        for signal in signals
        if signal["clv_vs_final"] is not None
    ]
    return {
        "analysis_version": "prematch-observations-v2",
        "matches_total": len(coverage),
        "matches_without_had": sum(x["status"] == "no_had_snapshots" for x in coverage),
        "matches_aligned": sum(x["aligned_pairs"] > 0 for x in coverage),
        "match_coverage": coverage,
        "matches_with_both_sides": matched,
        "matches_unmatched": unmatched,
        "match_methods": dict(sorted(match_methods.items())),
        "aligned_snapshot_pairs": aligned_pairs,
        "signal_threshold": SIGNAL_EDGE_THRESHOLD,
        "signals": len(signals),
        "clv_mean": round(sum(clvs) / len(clvs), 4) if clvs else None,
        "clv_positive_share": round(
            sum(1 for value in clvs if value > 0) / len(clvs), 4
        ) if clvs else None,
        "signal_details": sorted(
            signals,
            key=lambda item: (
                item["clv_vs_final"] is not None,
                item["clv_vs_final"] if item["clv_vs_final"] is not None else item["edge_at_signal"],
            ),
            reverse=True,
        ),
    }
