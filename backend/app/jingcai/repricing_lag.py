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
from typing import Any

from ..database import connect
from .team_names import JC_TO_CONSENSUS

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
               s.odd_draw::float8 AS d, s.odd_away::float8 AS a, s.captured_at_utc
        FROM jingcai.jc_odds_snapshots s
        JOIN jingcai.jc_matches m USING (jc_match_id)
        WHERE s.pool_code = 'had'
        ORDER BY s.jc_match_id, s.captured_at_utc
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
                "snaps": [],
            },
        )
        entry["snaps"].append(
            {"h": row["h"], "d": row["d"], "a": row["a"], "t": row["captured_at_utc"]}
        )
    return matches


def _consensus_trajectory(connection: Any, home_en: str, away_en: str) -> list[dict[str, Any]]:
    for bookmaker in SHARP_BOOKMAKERS:
        rows = connection.execute(
            """
            SELECT odd_home::float8 AS h, odd_draw::float8 AS d,
                   odd_away::float8 AS a, captured_at_utc AS t
            FROM jingcai.consensus_odds_snapshots
            WHERE home_team = %s AND away_team = %s AND bookmaker = %s
            ORDER BY captured_at_utc
            """,
            (home_en, away_en, bookmaker),
        ).fetchall()
        if rows:
            return [dict(row) for row in rows]
    return []


def analyze_repricing_lag() -> dict[str, Any]:
    signals: list[dict[str, Any]] = []
    matched = unmatched = aligned_pairs = 0
    with connect() as connection:
        for jc_match in _jc_trajectories(connection).values():
            home_en = JC_TO_CONSENSUS.get(jc_match["home"])
            away_en = JC_TO_CONSENSUS.get(jc_match["away"])
            consensus = (
                _consensus_trajectory(connection, home_en, away_en)
                if home_en and away_en
                else []
            )
            if not consensus:
                unmatched += 1
                continue
            matched += 1
            final = consensus[-1]
            p_final = _devig(final["h"], final["d"], final["a"])
            seen_signal_keys: set[tuple[int, str]] = set()
            for snap in jc_match["snaps"]:
                latest = None
                for cs in consensus:
                    if cs["t"] <= snap["t"]:
                        latest = cs
                    else:
                        break
                if latest is None:
                    continue
                gap_hours = (snap["t"] - latest["t"]).total_seconds() / 3600.0
                if gap_hours > MAX_ALIGN_GAP_HOURS:
                    continue
                aligned_pairs += 1
                p_now = _devig(latest["h"], latest["d"], latest["a"])
                jc_odds = (snap["h"], snap["d"], snap["a"])
                for i, label in enumerate(("home", "draw", "away")):
                    edge = p_now[i] * jc_odds[i] - 1.0
                    if edge <= SIGNAL_EDGE_THRESHOLD:
                        continue
                    key = (id(jc_match), label)
                    if key in seen_signal_keys:
                        continue  # 每场每方向只记首个信号
                    seen_signal_keys.add(key)
                    clv = p_final[i] * jc_odds[i] - 1.0
                    signals.append(
                        {
                            "match": f"{jc_match['match_num']} {jc_match['home']} vs {jc_match['away']}",
                            "league": jc_match["league"],
                            "date": jc_match["date"],
                            "outcome": label,
                            "jc_odd": jc_odds[i],
                            "edge_at_signal": round(edge, 4),
                            "align_gap_hours": round(gap_hours, 2),
                            "clv_vs_final": round(clv, 4),
                        }
                    )
    clvs = [signal["clv_vs_final"] for signal in signals]
    return {
        "matches_with_both_sides": matched,
        "matches_unmatched": unmatched,
        "aligned_snapshot_pairs": aligned_pairs,
        "signal_threshold": SIGNAL_EDGE_THRESHOLD,
        "signals": len(signals),
        "clv_mean": round(sum(clvs) / len(clvs), 4) if clvs else None,
        "clv_positive_share": round(
            sum(1 for value in clvs if value > 0) / len(clvs), 4
        ) if clvs else None,
        "signal_details": sorted(
            signals, key=lambda item: item["clv_vs_final"], reverse=True
        )[:40],
    }
