"""竞彩最终固定赔率 vs 国际封盘共识的历史价差回测。

数据两侧:
- jingcai.jc_result_history: 竞彩胜平负最终固定赔率(≈停售价)+彩果;
- core.historical_matches + closing odds: 同场国际封盘去水概率+赛果。

关联: 联赛(五大) + 日期(竞彩为北京时间, 欧洲晚场对应 +1 天,
允许 0/+1 日偏移) + 队名(football-data 英文名经 localize_team 转中文,
与竞彩简称/全称比对)。

策略评估: 以国际封盘去水概率为真实概率, EV_i = p_i * jc_odd_i - 1,
超过阈值即(纸面)下注 1 单位, 用真实赛果结算。这是研究诊断,
不构成投注建议; 竞彩最终价与封盘价的时间差是已知近似。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from ..database import connect
from .team_names import JC_TO_FD

BIG_FIVE_ABBRS = ("英超", "西甲", "德甲", "意甲", "法甲")
ABBR_TO_CODE = {"英超": "PL", "西甲": "PD", "德甲": "BL1", "意甲": "SA", "法甲": "FL1"}
RESULT_INDEX = {"H": 0, "D": 1, "A": 2}
EV_THRESHOLDS = (0.0, 0.02, 0.05)


def _load_jc_rows(connection: Any, start_on: date, end_on: date) -> list[dict[str, Any]]:
    return list(
        connection.execute(
            """
            SELECT official_match_id, match_date, league_abbr, home_team, away_team,
                   home_team_full, away_team_full,
                   odd_home::float8 AS odd_home, odd_draw::float8 AS odd_draw,
                   odd_away::float8 AS odd_away, win_flag, ft_score, pool_status
            FROM jingcai.jc_result_history
            WHERE league_abbr = ANY(%s)
              AND match_date BETWEEN %s AND %s
              AND odd_home IS NOT NULL AND odd_draw IS NOT NULL AND odd_away IS NOT NULL
              AND win_flag IN ('H','D','A')
            """,
            (list(BIG_FIVE_ABBRS), start_on, end_on),
        ).fetchall()
    )


def _load_fd_rows(connection: Any, start_on: date, end_on: date) -> list[dict[str, Any]]:
    return list(
        connection.execute(
            """
            SELECT hm.hist_match_id, hm.competition_code, hm.match_date,
                   hm.home_team, hm.away_team, hm.full_time_result,
                   ho.odd_home::float8 AS c_home, ho.odd_draw::float8 AS c_draw,
                   ho.odd_away::float8 AS c_away
            FROM core.historical_matches hm
            JOIN core.historical_odds ho
              ON ho.hist_match_id = hm.hist_match_id
             AND ho.bookmaker = 'MarketAverage'
             AND ho.market_type = '1X2'
             AND ho.price_timing = 'closing'
            WHERE hm.competition_code IN ('PL','PD','BL1','SA','FL1')
              AND hm.match_date BETWEEN %s AND %s
              AND hm.result_status NOT IN ('awarded', 'abandoned')
            """,
            (start_on - timedelta(days=1), end_on, ),
        ).fetchall()
    )


def _fd_index(fd_rows: list[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
    index: dict[tuple, dict[str, Any]] = {}
    for row in fd_rows:
        code = row["competition_code"]
        # 竞彩 matchDate 为北京时间, 欧洲比赛通常等于当地日期或 +1 天
        for offset in (0, 1):
            key = (
                code,
                row["match_date"] + timedelta(days=offset),
                row["home_team"],
                row["away_team"],
            )
            index.setdefault(key, row)
    return index


def _match_jc_to_fd(
    jc_rows: list[dict[str, Any]], fd_index: dict[tuple, dict[str, Any]]
) -> tuple[list[tuple[dict, dict]], list[dict]]:
    paired: list[tuple[dict, dict]] = []
    unmatched: list[dict] = []
    for jc in jc_rows:
        code = ABBR_TO_CODE[jc["league_abbr"]]
        home_en = JC_TO_FD.get(jc["home_team"])
        away_en = JC_TO_FD.get(jc["away_team"])
        found = None
        if home_en and away_en:
            found = fd_index.get((code, jc["match_date"], home_en, away_en))
        if found:
            paired.append((jc, found))
        else:
            unmatched.append(jc)
    return paired, unmatched


def _season_label(match_date: date) -> str:
    year = match_date.year if match_date.month >= 7 else match_date.year - 1
    return f"{year}/{str(year + 1)[-4:]}"


def backtest_spread(start_on: date, end_on: date) -> dict[str, Any]:
    with connect() as connection:
        jc_rows = _load_jc_rows(connection, start_on, end_on)
        fd_rows = _load_fd_rows(connection, start_on, end_on)
    fd_index = _fd_index(fd_rows)
    paired, unmatched = _match_jc_to_fd(jc_rows, fd_index)

    result_conflicts = 0
    evs: list[float] = []
    bets: dict[float, list[dict[str, Any]]] = {t: [] for t in EV_THRESHOLDS}
    per_outcome_ev: dict[str, list[float]] = defaultdict(list)
    overrounds: list[float] = []
    for jc, fd in paired:
        # 竞彩彩果与 football-data 赛果交叉核验
        if jc["win_flag"] != fd["full_time_result"]:
            result_conflicts += 1
            continue
        inverse = (1 / fd["c_home"], 1 / fd["c_draw"], 1 / fd["c_away"])
        total = sum(inverse)
        consensus = tuple(v / total for v in inverse)
        jc_odds = (jc["odd_home"], jc["odd_draw"], jc["odd_away"])
        overrounds.append(sum(1 / o for o in jc_odds) - 1)
        actual = RESULT_INDEX[fd["full_time_result"]]
        for i, label in enumerate(("home", "draw", "away")):
            ev = consensus[i] * jc_odds[i] - 1
            evs.append(ev)
            per_outcome_ev[label].append(ev)
            for threshold in EV_THRESHOLDS:
                if ev > threshold:
                    pnl = (jc_odds[i] - 1.0) if i == actual else -1.0
                    bets[threshold].append(
                        {
                            "d": str(jc["match_date"]),
                            "league": jc["league_abbr"],
                            "outcome": label,
                            "odd": jc_odds[i],
                            "ev": round(ev, 4),
                            "pnl": pnl,
                            "season": _season_label(jc["match_date"]),
                        }
                    )


    def _bet_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {"bets": 0, "roi": None}
        pnl = sum(row["pnl"] for row in rows)
        by_season: dict[str, list[float]] = defaultdict(list)
        by_outcome: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            by_season[row["season"]].append(row["pnl"])
            by_outcome[row["outcome"]].append(row["pnl"])
        return {
            "bets": len(rows),
            "pnl_units": round(pnl, 2),
            "roi": round(pnl / len(rows), 4),
            "hit_rate": round(
                sum(1 for row in rows if row["pnl"] > 0) / len(rows), 4
            ),
            "avg_odd": round(sum(row["odd"] for row in rows) / len(rows), 2),
            "by_season": {
                season: {"bets": len(v), "roi": round(sum(v) / len(v), 4)}
                for season, v in sorted(by_season.items())
            },
            "by_outcome": {
                outcome: {"bets": len(v), "roi": round(sum(v) / len(v), 4)}
                for outcome, v in sorted(by_outcome.items())
            },
        }

    evs_sorted = sorted(evs)

    def _pct(q: float) -> float:
        return round(evs_sorted[int(q * (len(evs_sorted) - 1))], 4) if evs_sorted else None

    return {
        "window": [str(start_on), str(end_on)],
        "jc_matches": len(jc_rows),
        "fd_matches": len(fd_rows),
        "paired": len(paired),
        "pair_rate": round(len(paired) / len(jc_rows), 4) if jc_rows else None,
        "result_conflicts": result_conflicts,
        "jc_overround_avg": round(sum(overrounds) / len(overrounds), 4) if overrounds else None,
        "ev_distribution": {
            "n": len(evs),
            "mean": round(sum(evs) / len(evs), 4) if evs else None,
            "p50": _pct(0.50), "p90": _pct(0.90), "p99": _pct(0.99),
            "max": _pct(1.0),
            "share_positive": round(
                sum(1 for e in evs if e > 0) / len(evs), 4
            ) if evs else None,
        },
        "ev_mean_by_outcome": {
            k: round(sum(v) / len(v), 4) for k, v in sorted(per_outcome_ev.items())
        },
        "strategies": {
            f"ev>{int(t*100)}%": _bet_summary(bets[t]) for t in EV_THRESHOLDS
        },
        "unmatched_sample": [
            f"{u['match_date']} {u['league_abbr']} {u['home_team']} vs {u['away_team']}"
            for u in unmatched[:15]
        ],
    }
