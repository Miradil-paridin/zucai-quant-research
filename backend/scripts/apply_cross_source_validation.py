from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import connect, initialize_database  # noqa: E402


LEAGUE_CODES = {
    "Premier League": "PL",
    "Bundesliga": "BL1",
    "Serie A": "SA",
    "La Liga": "PD",
    "Ligue 1": "FL1",
}


class CrossSourceReportError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply a hash-pinned, independently generated cross-source result audit."
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "data" / "source_samples" / "cross_source_validation.json",
    )
    return parser.parse_args()


def verify_report(report_path: Path) -> tuple[dict[str, Any], str]:
    content = report_path.read_bytes()
    report_sha256 = hashlib.sha256(content).hexdigest()
    report = json.loads(content)
    summary = report.get("summary") or {}
    required = {
        "football_data_matches": 1752,
        "football_data_org_matches": 1752,
        "football_data_org_verified_full_time_scores": 1752,
        "football_data_org_full_time_score_mismatches": 0,
        "unmatched_matches": 0,
        "fatal_error_count": 0,
    }
    mismatches = {
        key: {"expected": expected, "actual": summary.get(key)}
        for key, expected in required.items()
        if summary.get(key) != expected
    }
    if mismatches:
        raise CrossSourceReportError(f"report summary failed required checks: {mismatches}")
    if summary.get("status") not in {"PASS", "PASS_WITH_WARNINGS"}:
        raise CrossSourceReportError(f"report status is not acceptable: {summary.get('status')!r}")

    sample_root = report_path.parent
    for check in report.get("checksums") or []:
        source_file = sample_root / check["file"]
        if not source_file.is_file():
            raise CrossSourceReportError(f"pinned source file is missing: {source_file}")
        actual = hashlib.sha256(source_file.read_bytes()).hexdigest().upper()
        expected = str(check["expected_sha256"]).upper()
        if actual != expected or not check.get("matched"):
            raise CrossSourceReportError(
                f"source file hash mismatch for {source_file}: expected {expected}, got {actual}"
            )
    return report, report_sha256


def add_quality_flag(connection, where_sql: str, params: tuple[Any, ...], flag: str) -> int:
    result = connection.execute(
        f"""
        UPDATE core.historical_matches
        SET quality_flags = CASE
            WHEN quality_flags ? %s THEN quality_flags
            ELSE quality_flags || jsonb_build_array(%s::text)
        END
        WHERE {where_sql}
        """,
        (flag, flag, *params),
    )
    return result.rowcount


def apply_report(report_path: Path) -> dict[str, Any]:
    report, report_sha256 = verify_report(report_path)
    initialize_database()
    summary = report["summary"]
    warnings = report.get("warnings") or []
    generated_at = report.get("generated_at_utc")
    checked = 0
    awarded = 0

    with connect() as connection:
        connection.execute(
            """
            INSERT INTO audit.historical_validation_reports (
                report_sha256, report_path, primary_source, verification_sources,
                season_label, matches_checked, status, summary, warnings, generated_at_utc
            )
            VALUES (%s, %s, 'football-data.co.uk', %s, '2024/2025', %s, %s, %s, %s, %s)
            ON CONFLICT (report_sha256)
            DO UPDATE SET
                report_path = EXCLUDED.report_path,
                summary = EXCLUDED.summary,
                warnings = EXCLUDED.warnings,
                applied_at_utc = now()
            """,
            (
                report_sha256,
                str(report_path.resolve()),
                Jsonb(["football-data.org", "OpenFootball", "official club match centre"]),
                int(summary["football_data_org_verified_full_time_scores"]),
                "pass_with_warnings" if warnings else "pass",
                Jsonb(summary),
                Jsonb(warnings),
                datetime.fromisoformat(generated_at) if generated_at else None,
            ),
        )

        for league in report.get("leagues") or []:
            code = LEAGUE_CODES.get(league.get("league"))
            if not code:
                raise CrossSourceReportError(f"unknown report league {league.get('league')!r}")
            expected_matches = int(league["football_data_records"])
            actual = connection.execute(
                """
                SELECT count(*) AS count
                FROM core.historical_matches
                WHERE competition_code = %s AND season_label = '2024/2025'
                """,
                (code,),
            ).fetchone()["count"]
            if int(actual) != expected_matches:
                raise CrossSourceReportError(
                    f"database coverage mismatch for {code}: expected {expected_matches}, got {actual}"
                )
            result = connection.execute(
                """
                UPDATE core.historical_matches
                SET result_status = 'verified_normal',
                    result_verification_source = 'football-data.org',
                    result_verification_report_sha256 = %s,
                    result_verified_at_utc = now(),
                    quality_flags = CASE
                        WHEN quality_flags ? 'cross_source_full_time_verified' THEN quality_flags
                        ELSE quality_flags || '["cross_source_full_time_verified"]'::jsonb
                    END
                WHERE competition_code = %s AND season_label = '2024/2025'
                """,
                (report_sha256, code),
            )
            checked += result.rowcount

            for item in league.get("football_data_org_awarded_matches") or []:
                result = connection.execute(
                    """
                    UPDATE core.historical_matches
                    SET result_status = 'awarded',
                        quality_flags = CASE
                            WHEN quality_flags ? 'awarded_result_statistics_not_full_90'
                                THEN quality_flags
                            ELSE quality_flags || '["awarded_result_statistics_not_full_90"]'::jsonb
                        END
                    WHERE competition_code = %s
                      AND season_label = '2024/2025'
                      AND match_date = %s
                      AND home_team = %s
                      AND away_team = %s
                    """,
                    (code, item["date"], item["home"], item["away"]),
                )
                if result.rowcount != 1:
                    raise CrossSourceReportError(
                        f"awarded match did not resolve uniquely: {code} {item}"
                    )
                awarded += 1

        if any("Fiorentina" in warning and "Inter" in warning for warning in warnings):
            affected = add_quality_flag(
                connection,
                """
                competition_code = 'SA'
                AND season_label = '2024/2025'
                AND match_date = %s
                AND home_team = 'Fiorentina'
                AND away_team = 'Inter'
                """,
                ("2025-02-06",),
                "half_time_cross_source_conflict_official_review_prefers_primary",
            )
            if affected != 1:
                raise CrossSourceReportError("Fiorentina-Inter review flag did not resolve uniquely")
        connection.commit()

    return {
        "status": "succeeded",
        "report_sha256": report_sha256,
        "matches_cross_source_verified": checked,
        "awarded_matches": awarded,
        "warnings": warnings,
    }


def main() -> int:
    args = parse_args()
    result = apply_report(args.report.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
