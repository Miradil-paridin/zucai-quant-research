"""Persist operational data-quality checks for the live research pipeline."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from psycopg.types.json import Jsonb

from .database import connect, PROJECT_ROOT
from .collector_runtime import load_runtime_state
from .jingcai.consensus import SPORT_KEY_BY_LEAGUE
from .jingcai.matching import resolve_consensus_event


def _freshness_status(age_hours: float | None, warn_hours: float, fail_hours: float) -> str:
    if age_hours is None or age_hours > fail_hours:
        return "fail"
    if age_hours > warn_hours:
        return "warn"
    return "pass"


def run_data_quality_checks() -> dict[str, Any]:
    now_utc = datetime.now(UTC)
    checks: list[dict[str, Any]] = []
    with connect() as connection:
        summary = connection.execute(
            """
            SELECT
              count(*) FILTER (WHERE kickoff_utc >= now()) AS future_core_fixtures,
              count(*) FILTER (
                WHERE kickoff_utc >= now() AND EXISTS (
                  SELECT 1 FROM core.source_entity_mappings sem
                  WHERE sem.entity_type = 'match' AND sem.canonical_id = core.matches.match_id
                    AND sem.source_name IN ('api-football', 'football-data.org')
                )
              ) AS future_primary_fixtures,
              count(*) FILTER (
                WHERE kickoff_utc >= now() AND EXISTS (
                  SELECT 1 FROM core.source_entity_mappings sem
                  WHERE sem.entity_type = 'match' AND sem.canonical_id = core.matches.match_id
                    AND sem.source_name = 'sporttery'
                )
              ) AS future_fallback_fixtures,
              count(*) FILTER (
                WHERE status = 'live' AND kickoff_utc < now() - interval '6 hours'
              ) AS stale_live_fixtures,
              (SELECT max(last_observed_at_utc) FROM jingcai.jc_odds_snapshots)
                AS latest_jc_odds,
              (SELECT max(last_observed_at_utc) FROM jingcai.consensus_odds_snapshots)
                AS latest_consensus_odds,
              (SELECT max(match_date) FROM jingcai.jc_result_history)
                AS latest_jc_result,
              (now() AT TIME ZONE 'Asia/Shanghai')::date AS local_today
            FROM core.matches
            """
        ).fetchone()

        future_count = int(summary["future_core_fixtures"] or 0)
        primary_count = int(summary["future_primary_fixtures"] or 0)
        fallback_count = int(summary["future_fallback_fixtures"] or 0)
        checks.append(
            _check(
                "core_future_fixture_coverage",
                "core.matches",
                "future_fixture_count",
                future_count,
                1,
                "pass" if primary_count >= 1 else "warn" if fallback_count >= 1 else "fail",
                {
                    "primary_fixtures": primary_count,
                    "fallback_fixtures": fallback_count,
                    "fallback_coverage": "selected_for_sale",
                },
            )
        )
        stale_live = int(summary["stale_live_fixtures"] or 0)
        checks.append(
            _check(
                "core_stale_live_status",
                "core.matches",
                "stale_live_count",
                stale_live,
                0,
                "pass" if stale_live == 0 else "fail",
            )
        )

        for name, column, warn_hours, fail_hours in (
            ("jingcai_odds_freshness", "latest_jc_odds", 0.5, 1.5),
            ("consensus_odds_freshness", "latest_consensus_odds", 6.0, 24.0),
        ):
            observed = summary[column]
            age_hours = (
                (now_utc - observed).total_seconds() / 3600.0 if observed else None
            )
            checks.append(
                _check(
                    name,
                    "odds_snapshot",
                    "age_hours",
                    age_hours,
                    warn_hours,
                    _freshness_status(age_hours, warn_hours, fail_hours),
                    {"observed_at_utc": str(observed) if observed else None},
                )
            )

        latest_result = summary["latest_jc_result"]
        result_lag = (
            (summary["local_today"] - latest_result).days if latest_result else None
        )
        result_status = (
            "fail" if result_lag is None or result_lag > 7
            else "warn" if result_lag > 2
            else "pass"
        )
        checks.append(
            _check(
                "jingcai_result_freshness",
                "jingcai.jc_result_history",
                "lag_days",
                result_lag,
                2,
                result_status,
                {"latest_match_date": str(latest_result) if latest_result else None},
            )
        )

        rows = connection.execute(
            """
            SELECT match_num_str, league_name, home_team, away_team,
                   match_date_local, match_time_local
            FROM jingcai.jc_matches
            WHERE match_date_local BETWEEN
                  (now() AT TIME ZONE 'Asia/Shanghai')::date - 1
              AND (now() AT TIME ZONE 'Asia/Shanghai')::date + 2
              AND league_name = ANY(%s)
            """,
            (list(SPORT_KEY_BY_LEAGUE),),
        ).fetchall()
        linked = sum(
            resolve_consensus_event(
                connection,
                league_name=row["league_name"],
                home_team=row["home_team"],
                away_team=row["away_team"],
                match_date_local=row["match_date_local"],
                match_time_local=row["match_time_local"],
            )
            is not None
            for row in rows
        )
        coverage = linked / len(rows) if rows else None
        coverage_status = (
            "fail" if coverage is None or coverage < 0.5
            else "warn" if coverage < 0.8
            else "pass"
        )
        checks.append(
            _check(
                "jingcai_consensus_link_coverage",
                "jingcai.jc_matches",
                "linked_share",
                coverage,
                0.8,
                coverage_status,
                {"eligible_matches": len(rows), "linked_matches": linked},
            )
        )

        # Inspect the latest UCL match window even when it has left the sale list.
        ucl = connection.execute(
            """SELECT league_name, home_team, away_team, match_date_local, match_time_local
               FROM jingcai.jc_matches
               WHERE league_name = '欧洲冠军联赛'
                 AND match_date_local >= (SELECT max(match_date_local) - 6
                     FROM jingcai.jc_matches WHERE league_name = '欧洲冠军联赛')"""
        ).fetchall()
        if ucl:
            ucl_linked = sum(resolve_consensus_event(connection, **dict(row)) is not None for row in ucl)
            checks.append(_check("ucl_consensus_link_coverage", "jingcai.jc_matches", "linked_share",
                ucl_linked / len(ucl), 1, "pass" if ucl_linked == len(ucl) else "warn",
                {"eligible_matches": len(ucl), "linked_matches": ucl_linked,
                 "coverage": "latest_jingcai_ucl_window_not_all_official_fixtures"}))

        report = load_runtime_state(PROJECT_ROOT / "output/runtime/repricing_lag_latest.json")
        try:
            report_age = (now_utc - datetime.fromisoformat(report["generated_at_utc"])).total_seconds() / 3600
        except (KeyError, ValueError, TypeError):
            report_age = None
        checks.append(_check("repricing_report_freshness", "analysis_report", "age_hours",
            report_age, 8 * 24, _freshness_status(report_age, 8 * 24, 15 * 24)))
        if ucl and report.get("match_coverage"):
            latest = max(str(row["match_date_local"]) for row in ucl)
            earliest = min(str(row["match_date_local"]) for row in ucl)
            covered = [x for x in report["match_coverage"] if x["league"] == "欧洲冠军联赛" and earliest <= x["date"] <= latest]
            eligible = [x for x in covered if x["status"] != "no_had_snapshots"]
            aligned = sum(x["aligned_pairs"] > 0 for x in eligible)
            checks.append(_check("ucl_prematch_alignment", "analysis_report", "aligned_share",
                aligned / len(eligible) if eligible else None, 1,
                "pass" if eligible and aligned == len(eligible) else "warn",
                {"had_matches": len(eligible), "aligned_matches": aligned,
                 "without_had": len(covered) - len(eligible), "report_at_utc": report.get("generated_at_utc")}))

        for check in checks:
            connection.execute(
                """
                INSERT INTO audit.data_quality_checks (
                    check_name, entity_type, scope_key, metric_name,
                    metric_value, threshold_value, status, details
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    check["check_name"],
                    check["entity_type"],
                    "live_pipeline",
                    check["metric_name"],
                    check["metric_value"],
                    check["threshold_value"],
                    check["status"],
                    Jsonb(check["details"]),
                ),
            )
        connection.commit()
    return {
        "checked_at_utc": now_utc.isoformat(),
        "status": "fail" if any(c["status"] == "fail" for c in checks)
        else "warn" if any(c["status"] == "warn" for c in checks)
        else "pass",
        "checks": checks,
    }


def _check(
    check_name: str,
    entity_type: str,
    metric_name: str,
    metric_value: float | int | None,
    threshold_value: float | int,
    status: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "check_name": check_name,
        "entity_type": entity_type,
        "metric_name": metric_name,
        "metric_value": metric_value,
        "threshold_value": threshold_value,
        "status": status,
        "details": details or {},
    }
