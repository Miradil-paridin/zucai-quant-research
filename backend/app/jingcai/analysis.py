"""竞彩 vs 国际共识价差分析。

对每场可关联的竞彩比赛:
- 竞彩侧取最新 had(胜平负)快照, 保留原始可成交赔率;
- 共识侧取基准 bookmaker(默认 pinnacle, 缺失时按候选顺序回退)最新快照;
- 以共识去水概率为"真实概率"估计, 计算按竞彩赔率下注每个方向的
  期望值 EV = p_consensus * odd_jc - 1。

这是研究诊断, 不是投注建议。EV 为正只说明竞彩价格相对国际共识偏高,
是否可持续需要更长时间窗口与赛果结算验证。
"""

from __future__ import annotations

from typing import Any

from ..database import connect
from .team_names import JC_TO_CONSENSUS

SHARP_BOOKMAKERS = ("pinnacle", "betfair_ex_eu", "marathonbet", "onexbet")


def _devig(odd_home: float, odd_draw: float, odd_away: float) -> tuple[float, float, float, float]:
    """返回 (p_home, p_draw, p_away, overround)。比例去水。"""

    inverse = (1 / odd_home, 1 / odd_draw, 1 / odd_away)
    total = sum(inverse)
    return inverse[0] / total, inverse[1] / total, inverse[2] / total, total - 1.0


def _latest_jc_snapshots(connection: Any) -> list[dict[str, Any]]:
    return list(
        connection.execute(
            """
            SELECT DISTINCT ON (s.jc_match_id)
                   s.jc_match_id, m.official_match_id, m.match_num_str,
                   m.league_name, m.home_team, m.away_team, m.match_date_local,
                   s.odd_home::float8 AS odd_home, s.odd_draw::float8 AS odd_draw,
                   s.odd_away::float8 AS odd_away, s.captured_at_utc
            FROM jingcai.jc_odds_snapshots s
            JOIN jingcai.jc_matches m USING (jc_match_id)
            WHERE s.pool_code = 'had'
            ORDER BY s.jc_match_id, s.captured_at_utc DESC, s.jc_snapshot_id DESC
            """
        ).fetchall()
    )


def _latest_consensus(connection: Any, home_en: str, away_en: str) -> dict[str, Any] | None:
    for bookmaker in SHARP_BOOKMAKERS:
        row = connection.execute(
            """
            SELECT bookmaker, odd_home::float8 AS odd_home, odd_draw::float8 AS odd_draw,
                   odd_away::float8 AS odd_away, captured_at_utc, commence_time_utc
            FROM jingcai.consensus_odds_snapshots
            WHERE home_team = %s AND away_team = %s AND bookmaker = %s
            ORDER BY captured_at_utc DESC, consensus_snapshot_id DESC
            LIMIT 1
            """,
            (home_en, away_en, bookmaker),
        ).fetchone()
        if row:
            return dict(row)
    return None


def analyze_spread() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    unmatched: list[str] = []
    with connect() as connection:
        for jc in _latest_jc_snapshots(connection):
            home_en = JC_TO_CONSENSUS.get(jc["home_team"])
            away_en = JC_TO_CONSENSUS.get(jc["away_team"])
            if not home_en or not away_en:
                unmatched.append(
                    f"{jc['match_num_str']} {jc['home_team']} vs {jc['away_team']} (缺队名映射)"
                )
                continue
            consensus = _latest_consensus(connection, home_en, away_en)
            if consensus is None:
                unmatched.append(
                    f"{jc['match_num_str']} {home_en} vs {away_en} (共识侧无该场快照)"
                )
                continue
            jc_p = _devig(jc["odd_home"], jc["odd_draw"], jc["odd_away"])
            cs_p = _devig(
                consensus["odd_home"], consensus["odd_draw"], consensus["odd_away"]
            )
            outcomes = []
            for index, label in enumerate(("home", "draw", "away")):
                jc_odd = jc[f"odd_{label}"]
                ev = cs_p[index] * jc_odd - 1.0
                outcomes.append(
                    {
                        "outcome": label,
                        "jc_odd": jc_odd,
                        "jc_prob": round(jc_p[index], 4),
                        "consensus_prob": round(cs_p[index], 4),
                        "prob_gap": round(jc_p[index] - cs_p[index], 4),
                        "ev": round(ev, 4),
                    }
                )
            rows.append(
                {
                    "match_num": jc["match_num_str"],
                    "league": jc["league_name"],
                    "match": f"{jc['home_team']} vs {jc['away_team']}",
                    "match_en": f"{home_en} vs {away_en}",
                    "match_date": str(jc["match_date_local"]),
                    "jc_captured_at": str(jc["captured_at_utc"]),
                    "consensus_bookmaker": consensus["bookmaker"],
                    "consensus_captured_at": str(consensus["captured_at_utc"]),
                    "jc_overround": round(jc_p[3], 4),
                    "consensus_overround": round(cs_p[3], 4),
                    "outcomes": outcomes,
                    "best_ev": max(item["ev"] for item in outcomes),
                }
            )
    rows.sort(key=lambda item: item["best_ev"], reverse=True)
    positive = [item for item in rows if item["best_ev"] > 0]
    return {
        "matches_analyzed": len(rows),
        "matches_unmatched": len(unmatched),
        "positive_ev_matches": len(positive),
        "unmatched": unmatched,
        "rows": rows,
    }
