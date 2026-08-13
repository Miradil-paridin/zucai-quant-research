#!/usr/bin/env python3
"""Read-only independent audit for the Nordic historical import.

The script does not import the production parser. It reconstructs every match
and closing 1X2 quote directly from FIN.csv, NOR.csv and SWE.csv, then compares
that reconstruction with PostgreSQL inside one repeatable-read/read-only
transaction.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import connect  # noqa: E402


AUDIT_VERSION = "nordic-database-audit-v1"
DIVISIONS = {
    "FIN": {
        "competition": "FI1",
        "country_code": "FI",
        "source_country": "Finland",
        "source_league": "Veikkausliiga",
    },
    "NOR": {
        "competition": "NO1",
        "country_code": "NO",
        "source_country": "Norway",
        "source_league": "Eliteserien",
    },
    "SWE": {
        "competition": "SE1",
        "country_code": "SE",
        "source_country": "Sweden",
        "source_league": "Allsvenskan",
    },
}
EXPECTED_ROWS = {"FIN": 2640, "NOR": 3489, "SWE": 3489}
PINNACLE_CUTOFF = date(2025, 7, 23)
EXPECTED_PINNACLE_QUARANTINE = {"FIN": 71, "NOR": 87, "SWE": 94}
EXPECTED_MARKET_QUARANTINE = {"FIN": 2, "NOR": 0, "SWE": 0}
REQUIRED_HEADERS = {
    "Country",
    "League",
    "Season",
    "Date",
    "Time",
    "Home",
    "Away",
    "HG",
    "AG",
    "Res",
    "PSCH",
    "PSCD",
    "PSCA",
    "MaxCH",
    "MaxCD",
    "MaxCA",
    "AvgCH",
    "AvgCD",
    "AvgCA",
    "BFECH",
    "BFECD",
    "BFECA",
    "B365CH",
    "B365CD",
    "B365CA",
}


@dataclass(frozen=True)
class OddsSpec:
    bookmaker: str
    home: str
    draw: str
    away: str


ODDS_SPECS = (
    OddsSpec("Pinnacle", "PSCH", "PSCD", "PSCA"),
    OddsSpec("MarketMax", "MaxCH", "MaxCD", "MaxCA"),
    OddsSpec("MarketAverage", "AvgCH", "AvgCD", "AvgCA"),
    OddsSpec("BetfairExchange", "BFECH", "BFECD", "BFECA"),
    OddsSpec("Bet365", "B365CH", "B365CD", "B365CA"),
)


@dataclass
class Finding:
    severity: str
    code: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


class Audit:
    def __init__(self) -> None:
        self.findings: list[Finding] = []
        self.occurrences: Counter[str] = Counter()
        self.metrics: dict[str, Any] = {}

    def add(
        self,
        severity: str,
        code: str,
        message: str,
        evidence: dict[str, Any] | None = None,
        *,
        sample_limit: int = 12,
    ) -> None:
        self.occurrences[code] += 1
        if self.occurrences[code] <= sample_limit:
            self.findings.append(Finding(severity, code, message, evidence or {}))

    def fail(self, code: str, message: str, evidence: dict[str, Any] | None = None) -> None:
        self.add("fatal", code, message, evidence)

    def warn(self, code: str, message: str, evidence: dict[str, Any] | None = None) -> None:
        self.add("warning", code, message, evidence)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only PostgreSQL audit for FIN/NOR/SWE source CSV imports."
    )
    parser.add_argument(
        "--source-report",
        type=Path,
        default=PROJECT_ROOT / "data" / "source_samples" / "nordic_source_validation.json",
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    if isinstance(value, Decimal):
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def normalize_team_name(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", folded)


def decode_csv(content: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("CSV is neither UTF-8 nor Windows-1252")


def read_csv(path: Path) -> tuple[dict[int, dict[str, str]], str, list[str]]:
    text, encoding = decode_csv(path.read_bytes())
    reader = csv.DictReader(text.splitlines())
    if reader.fieldnames is None:
        raise ValueError("CSV header is missing")
    rows: dict[int, dict[str, str]] = {}
    for row_number, source_row in enumerate(reader, start=2):
        raw = {
            str(key): value or ""
            for key, value in source_row.items()
            if key is not None
        }
        if any(value.strip() for value in raw.values()):
            rows[row_number] = raw
    return rows, encoding, [str(item) for item in reader.fieldnames if item is not None]


def parse_date(value: str) -> date:
    for pattern in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            continue
    raise ValueError(f"invalid date {value!r}")


def parse_time(value: str) -> time | None:
    raw = value.strip()
    if not raw:
        return None
    for pattern in ("%H:%M", "%H.%M"):
        try:
            return datetime.strptime(raw, pattern).time()
        except ValueError:
            continue
    raise ValueError(f"invalid time {value!r}")


def decimal_or_none(value: str) -> Decimal | None:
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = Decimal(raw)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def row_hash(raw_row: dict[str, str]) -> str:
    return hashlib.sha256(canonical_json(raw_row).encode("utf-8")).hexdigest()


def expected_match(
    division: str,
    row_number: int,
    raw_row: dict[str, str],
) -> dict[str, Any]:
    row = {key.strip(): value.strip() for key, value in raw_row.items()}
    definition = DIVISIONS[division]
    if row["Country"] != definition["source_country"]:
        raise ValueError(f"unexpected Country {row['Country']!r}")
    if row["League"] != definition["source_league"]:
        raise ValueError(f"unexpected League {row['League']!r}")
    season = row["Season"]
    if not re.fullmatch(r"\d{4}", season):
        raise ValueError(f"invalid calendar season {season!r}")
    match_date = parse_date(row["Date"])
    if match_date.year != int(season):
        raise ValueError("date year does not equal calendar season")
    match_time = parse_time(row["Time"])
    home = row["Home"]
    away = row["Away"]
    home_norm = normalize_team_name(home)
    away_norm = normalize_team_name(away)
    if not home_norm or not away_norm or home_norm == away_norm:
        raise ValueError("invalid normalized teams")
    home_goals = int(row["HG"])
    away_goals = int(row["AG"])
    if home_goals < 0 or away_goals < 0:
        raise ValueError("negative score")
    result = "H" if home_goals > away_goals else "A" if home_goals < away_goals else "D"
    if row["Res"].upper() != result:
        raise ValueError("source result conflicts with score")
    flags: list[str] = []
    if match_time is not None:
        flags.append("source_time_timezone_unverified")
    if season == "2026":
        flags.append("partial_season_snapshot")
    return {
        "competition_code": definition["competition"],
        "source_division": division,
        "season_label": season,
        "match_date": match_date,
        "match_time_local": match_time,
        "source_timezone": "unknown",
        "home_team": home,
        "away_team": away,
        "home_team_norm": home_norm,
        "away_team_norm": away_norm,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "full_time_result": result,
        "ht_home_goals": None,
        "ht_away_goals": None,
        "half_time_result": None,
        "source_row_number": row_number,
        "source_row_hash": row_hash(raw_row),
        "match_stats": {},
        "raw_row": raw_row,
        "quality_flags": flags,
        "result_status": "unverified",
        "result_verification_source": None,
        "result_verification_report_sha256": None,
    }


def reconstruct_odds(
    raw_row: dict[str, str],
    match_date: date,
) -> tuple[dict[tuple[str, str, bool], dict[str, Any]], dict[str, Any]]:
    row = {key.strip(): value.strip() for key, value in raw_row.items()}
    max_values = [decimal_or_none(row.get(key, "")) for key in ("MaxCH", "MaxCD", "MaxCA")]
    avg_values = [decimal_or_none(row.get(key, "")) for key in ("AvgCH", "AvgCD", "AvgCA")]
    inconsistent = (
        all(value is not None for value in max_values + avg_values)
        and any(avg > maximum for avg, maximum in zip(avg_values, max_values, strict=True))
    )
    quotes: dict[tuple[str, str, bool], dict[str, Any]] = {}
    audit = {
        "incomplete": 0,
        "invalid": 0,
        "pinnacle_quarantined": 0,
        "market_consistency_quarantined": 0,
        "market_inconsistent": inconsistent,
    }
    for spec in ODDS_SPECS:
        raw_values = [row.get(spec.home, ""), row.get(spec.draw, ""), row.get(spec.away, "")]
        present = [bool(value.strip()) for value in raw_values]
        if not any(present):
            continue
        if not all(present):
            audit["incomplete"] += 1
            continue
        values = [decimal_or_none(value) for value in raw_values]
        if any(value is None or value <= 1 for value in values):
            audit["invalid"] += 1
            continue
        if spec.bookmaker == "Pinnacle" and match_date >= PINNACLE_CUTOFF:
            audit["pinnacle_quarantined"] += 1
            continue
        if inconsistent and spec.bookmaker in {"MarketMax", "MarketAverage"}:
            audit["market_consistency_quarantined"] += 1
            continue
        quotes[(spec.bookmaker, "1X2", True)] = {
            "price_timing": "closing",
            "point_in_time_safe": False,
            "line_value": None,
            "odd_home": values[0],
            "odd_draw": values[1],
            "odd_away": values[2],
            "odd_over": None,
            "odd_under": None,
            "source_columns": {
                spec.home: raw_values[0],
                spec.draw: raw_values[1],
                spec.away: raw_values[2],
            },
            "quality_flags": [],
        }
    return quotes, audit


def compare_fields(
    audit: Audit,
    code: str,
    context: dict[str, Any],
    expected: dict[str, Any],
    actual: dict[str, Any],
    fields: Iterable[str],
) -> None:
    differences = {}
    for field in fields:
        expected_value = canonicalize(expected.get(field))
        actual_value = canonicalize(actual.get(field))
        if field == "quality_flags":
            expected_value = sorted(expected_value or [])
            actual_value = sorted(actual_value or [])
        if expected_value != actual_value:
            differences[field] = {
                "expected": expected_value,
                "actual": actual_value,
            }
    if differences:
        audit.fail(code, "Persisted values differ from independent source reconstruction.", {
            **context,
            "differences": differences,
        })


def scalar(connection, query: str, params: tuple[Any, ...] = ()) -> int:
    row = connection.execute(query, params).fetchone()
    return int(next(iter(row.values())))


def load_source_report(path: Path, audit: Audit) -> tuple[dict[str, Any], str]:
    resolved = path.resolve()
    if not resolved.is_file():
        audit.fail("source_report_missing", "Independent Nordic source report is missing.", {
            "path": str(resolved),
        })
        return {}, ""
    content = resolved.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    try:
        payload = json.loads(content)
    except Exception as error:
        audit.fail("source_report_invalid", "Independent source report is invalid JSON.", {
            "error": str(error),
        })
        return {}, digest
    summary = payload.get("summary") or {}
    required_zero = (
        "missing_required_rows",
        "invalid_date_rows",
        "invalid_time_rows",
        "result_mismatch_rows",
        "duplicate_key_groups",
        "normalized_duplicate_key_groups",
    )
    if (
        int(summary.get("file_count", -1)) != 3
        or int(summary.get("total_rows", -1)) != 9618
        or any(int(summary.get(key, -1)) != 0 for key in required_zero)
        or summary.get("time_timezone") != "unknown"
    ):
        audit.fail("source_report_semantics", "Independent report does not support the required Nordic source facts.", {
            "summary": summary,
        })
    audit.metrics["source_report"] = {
        "path": str(resolved),
        "sha256": digest,
        "snapshot_response_date_utc": payload.get("source_snapshot_response_date_utc"),
        "status": summary.get("status"),
    }
    return payload, digest


def audit_database(connection, audit: Audit, source_report: dict[str, Any]) -> None:
    reference_files = {
        item["code"]: item for item in source_report.get("files") or []
    }
    sources = [
        dict(row)
        for row in connection.execute(
            """
            SELECT source_file_id, source_name, source_url, source_division,
                   season_label, http_etag, http_last_modified,
                   content_sha256, content_bytes, storage_path, parser_version,
                   row_count, status, validation_report
            FROM raw.source_files
            WHERE source_division IN ('FIN','NOR','SWE')
            ORDER BY source_division
            """
        )
    ]
    if len(sources) != 3 or {row["source_division"] for row in sources} != set(DIVISIONS):
        audit.fail("source_file_scope", "PostgreSQL does not contain exactly FIN/NOR/SWE source files.", {
            "actual": [row["source_division"] for row in sources],
        })

    match_fields = (
        "competition_code",
        "source_division",
        "season_label",
        "match_date",
        "match_time_local",
        "source_timezone",
        "home_team",
        "away_team",
        "home_team_norm",
        "away_team_norm",
        "home_goals",
        "away_goals",
        "full_time_result",
        "ht_home_goals",
        "ht_away_goals",
        "half_time_result",
        "source_row_number",
        "source_row_hash",
        "match_stats",
        "raw_row",
        "quality_flags",
        "result_status",
        "result_verification_source",
        "result_verification_report_sha256",
    )
    odds_fields = (
        "price_timing",
        "point_in_time_safe",
        "line_value",
        "odd_home",
        "odd_draw",
        "odd_away",
        "odd_over",
        "odd_under",
        "source_columns",
        "quality_flags",
    )
    reconstructed_matches = 0
    reconstructed_odds = 0
    quarantine = Counter()
    source_metrics: list[dict[str, Any]] = []

    for source in sources:
        division = source["source_division"]
        context = {"division": division, "source_file_id": int(source["source_file_id"])}
        reference = reference_files.get(division) or {}
        path = Path(source["storage_path"])
        if not path.is_file():
            audit.fail("source_file_missing", "Nordic source file is missing.", {
                **context,
                "path": str(path),
            })
            continue
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        expected_url = f"https://www.football-data.co.uk/new/{division}.csv"
        reference_http = reference.get("http") or {}
        provenance_differences = {}
        expected_provenance = {
            "source_name": "football-data.co.uk",
            "source_url": expected_url,
            "season_label": "calendar-2012-current",
            "content_sha256": digest,
            "content_bytes": len(content),
            "parser_version": "football-data-nordic-v1",
            "row_count": EXPECTED_ROWS[division],
            "status": "imported",
            "http_etag": reference_http.get("etag"),
            "http_last_modified": reference_http.get("last_modified"),
        }
        for key, expected in expected_provenance.items():
            actual = source.get(key)
            if canonicalize(actual) != canonicalize(expected):
                provenance_differences[key] = {
                    "expected": canonicalize(expected),
                    "actual": canonicalize(actual),
                }
        if reference:
            if str(reference.get("sha256", "")).lower() != digest:
                provenance_differences["independent_report_sha256"] = {
                    "expected": str(reference.get("sha256", "")).lower(),
                    "actual": digest,
                }
            if int(reference.get("bytes", -1)) != len(content):
                provenance_differences["independent_report_bytes"] = {
                    "expected": reference.get("bytes"),
                    "actual": len(content),
                }
            if int(reference.get("row_count", -1)) != EXPECTED_ROWS[division]:
                provenance_differences["independent_report_rows"] = {
                    "expected": reference.get("row_count"),
                    "actual": EXPECTED_ROWS[division],
                }
        else:
            provenance_differences["independent_report_entry"] = {
                "expected": division,
                "actual": None,
            }
        if provenance_differences:
            audit.fail("source_provenance", "Source/database/reference provenance mismatch.", {
                **context,
                "differences": provenance_differences,
            })

        try:
            rows, encoding, headers = read_csv(path)
        except Exception as error:
            audit.fail("source_parse", "Nordic source CSV cannot be parsed.", {
                **context,
                "error": str(error),
            })
            continue
        if not REQUIRED_HEADERS.issubset(set(headers)):
            audit.fail("source_schema", "Nordic source CSV lacks required headers.", {
                **context,
                "missing": sorted(REQUIRED_HEADERS - set(headers)),
            })
        if len(rows) != EXPECTED_ROWS[division]:
            audit.fail("source_rows", "Unexpected Nordic source row count.", {
                **context,
                "expected": EXPECTED_ROWS[division],
                "actual": len(rows),
            })

        matches = [
            dict(row)
            for row in connection.execute(
                """
                SELECT hm.*,
                       ht.normalized_name AS mapped_home_norm,
                       ht.country_code AS mapped_home_country,
                       at.normalized_name AS mapped_away_norm,
                       at.country_code AS mapped_away_country
                FROM core.historical_matches hm
                LEFT JOIN core.teams ht ON ht.team_id = hm.team_id_home
                LEFT JOIN core.teams at ON at.team_id = hm.team_id_away
                WHERE hm.source_file_id = %s
                ORDER BY hm.source_row_number
                """,
                (source["source_file_id"],),
            )
        ]
        if len(matches) != len(rows):
            audit.fail("match_count", "Persisted match count differs from source.", {
                **context,
                "source": len(rows),
                "database": len(matches),
            })
        actual_odds: dict[tuple[int, str, str, bool], dict[str, Any]] = {}
        for odds_row in connection.execute(
            """
            SELECT hm.source_row_number, ho.bookmaker, ho.market_type,
                   ho.is_closing, ho.price_timing, ho.point_in_time_safe,
                   ho.line_value, ho.odd_home, ho.odd_draw, ho.odd_away,
                   ho.odd_over, ho.odd_under, ho.source_columns, ho.quality_flags
            FROM core.historical_odds ho
            JOIN core.historical_matches hm ON hm.hist_match_id = ho.hist_match_id
            WHERE hm.source_file_id = %s
            ORDER BY hm.source_row_number, ho.bookmaker
            """,
            (source["source_file_id"],),
        ):
            item = dict(odds_row)
            key = (
                int(item["source_row_number"]),
                item["bookmaker"],
                item["market_type"],
                bool(item["is_closing"]),
            )
            if key in actual_odds:
                audit.fail("odds_duplicate", "Duplicate Nordic quote key.", {
                    **context,
                    "key": list(key),
                })
            actual_odds[key] = item

        expected_odds_keys: set[tuple[int, str, str, bool]] = set()
        per_file = Counter()
        whitespace_rows = 0
        partial_rows = 0
        for match in matches:
            row_number = int(match["source_row_number"])
            raw_row = rows.get(row_number)
            row_context = {**context, "row": row_number}
            if raw_row is None:
                audit.fail("source_row_reference", "Match references a missing source row.", row_context)
                continue
            if any(value != value.strip() for value in raw_row.values()):
                whitespace_rows += 1
            try:
                expected = expected_match(division, row_number, raw_row)
            except Exception as error:
                audit.fail("source_semantics", "Source row failed independent semantic validation.", {
                    **row_context,
                    "error": str(error),
                })
                continue
            compare_fields(audit, "match_source_mismatch", row_context, expected, match, match_fields)
            country = DIVISIONS[division]["country_code"]
            if (
                match["team_id_home"] is None
                or match["team_id_away"] is None
                or match["team_id_home"] == match["team_id_away"]
                or match["mapped_home_norm"] != expected["home_team_norm"]
                or match["mapped_away_norm"] != expected["away_team_norm"]
                or match["mapped_home_country"] != country
                or match["mapped_away_country"] != country
            ):
                audit.fail("team_mapping", "Nordic team mapping differs from source identity.", {
                    **row_context,
                    "home": [expected["home_team_norm"], match["mapped_home_norm"], match["mapped_home_country"]],
                    "away": [expected["away_team_norm"], match["mapped_away_norm"], match["mapped_away_country"]],
                })
            if expected["season_label"] == "2026":
                partial_rows += 1

            quotes, quote_audit = reconstruct_odds(raw_row, expected["match_date"])
            for key in (
                "incomplete",
                "invalid",
                "pinnacle_quarantined",
                "market_consistency_quarantined",
            ):
                per_file[key] += int(quote_audit[key])
                quarantine[key] += int(quote_audit[key])
            for quote_key, expected_quote in quotes.items():
                db_key = (row_number, *quote_key)
                expected_odds_keys.add(db_key)
                actual = actual_odds.get(db_key)
                if actual is None:
                    audit.fail("missing_quote", "Valid reconstructed quote is absent from PostgreSQL.", {
                        **row_context,
                        "quote": list(quote_key),
                    })
                    continue
                compare_fields(
                    audit,
                    "quote_source_mismatch",
                    {**row_context, "quote": list(quote_key)},
                    expected_quote,
                    actual,
                    odds_fields,
                )
        unexpected = sorted(set(actual_odds) - expected_odds_keys)
        if unexpected:
            audit.fail("unexpected_quote", "PostgreSQL contains Nordic quotes that should be absent or quarantined.", {
                **context,
                "count": len(unexpected),
                "sample": [list(item) for item in unexpected[:10]],
            })
        if per_file["pinnacle_quarantined"] != EXPECTED_PINNACLE_QUARANTINE[division]:
            audit.fail("pinnacle_quarantine_count", "Pinnacle quarantine count differs from policy.", {
                **context,
                "expected": EXPECTED_PINNACLE_QUARANTINE[division],
                "actual": per_file["pinnacle_quarantined"],
            })
        if per_file["market_consistency_quarantined"] != EXPECTED_MARKET_QUARANTINE[division]:
            audit.fail("market_quarantine_count", "Max/Avg structural quarantine count differs from policy.", {
                **context,
                "expected": EXPECTED_MARKET_QUARANTINE[division],
                "actual": per_file["market_consistency_quarantined"],
            })
        if per_file["incomplete"] or per_file["invalid"]:
            audit.fail("unexpected_quote_quality", "Unexpected incomplete or invalid Nordic quote group.", {
                **context,
                **dict(per_file),
            })

        validation = source["validation_report"] or {}
        expected_validation = {
            "sha256": digest,
            "content_bytes": len(content),
            "input_rows": len(rows),
            "valid_matches": len(rows),
            "rejected_count": 0,
            "source_timezone": "unknown",
            "pinnacle_quote_groups_quarantined": per_file["pinnacle_quarantined"],
            "market_consistency_quote_groups_quarantined": per_file["market_consistency_quarantined"],
            "incomplete_quote_groups_quarantined": per_file["incomplete"],
            "invalid_quote_groups_quarantined": per_file["invalid"],
        }
        validation_diff = {
            key: {"expected": value, "actual": validation.get(key)}
            for key, value in expected_validation.items()
            if canonicalize(value) != canonicalize(validation.get(key))
        }
        if validation_diff:
            audit.fail("validation_report", "Database validation report is not independently reproducible.", {
                **context,
                "differences": validation_diff,
            })

        reconstructed_matches += len(rows)
        reconstructed_odds += len(expected_odds_keys)
        source_metrics.append({
            **context,
            "path": str(path),
            "sha256": digest,
            "bytes": len(content),
            "encoding": encoding,
            "matches": len(rows),
            "odds": len(expected_odds_keys),
            "whitespace_rows_raw_preserved": whitespace_rows,
            "partial_2026_rows": partial_rows,
            **dict(per_file),
        })

    audit.metrics["source_files"] = source_metrics
    audit.metrics["reconstruction"] = {
        "source_files": len(sources),
        "matches": reconstructed_matches,
        "odds": reconstructed_odds,
        **dict(quarantine),
    }


def audit_global_constraints(connection, audit: Audit) -> None:
    duplicate_matches = scalar(
        connection,
        """
        SELECT count(*) FROM (
          SELECT source_division, season_label, match_date, home_team_norm, away_team_norm
          FROM core.historical_matches
          WHERE source_division IN ('FIN','NOR','SWE')
          GROUP BY 1,2,3,4,5 HAVING count(*) > 1
        ) duplicate_keys
        """,
    )
    duplicate_rows = scalar(
        connection,
        """
        SELECT count(*) FROM (
          SELECT source_file_id, source_row_number
          FROM core.historical_matches
          WHERE source_division IN ('FIN','NOR','SWE')
          GROUP BY 1,2 HAVING count(*) > 1
        ) duplicate_rows
        """,
    )
    duplicate_odds = scalar(
        connection,
        """
        SELECT count(*) FROM (
          SELECT ho.hist_match_id, ho.bookmaker, ho.market_type, ho.is_closing
          FROM core.historical_odds ho
          JOIN core.historical_matches hm ON hm.hist_match_id = ho.hist_match_id
          WHERE hm.source_division IN ('FIN','NOR','SWE')
          GROUP BY 1,2,3,4 HAVING count(*) > 1
        ) duplicate_odds
        """,
    )
    result_errors = scalar(
        connection,
        """
        SELECT count(*) FROM core.historical_matches
        WHERE source_division IN ('FIN','NOR','SWE')
          AND full_time_result <> CASE
            WHEN home_goals > away_goals THEN 'H'
            WHEN home_goals < away_goals THEN 'A'
            ELSE 'D'
          END
        """,
    )
    mapping_errors = scalar(
        connection,
        """
        SELECT count(*) FROM core.historical_matches hm
        LEFT JOIN core.team_aliases home_alias
          ON home_alias.source_name = 'football-data.co.uk'
         AND home_alias.competition_code = hm.competition_code
         AND home_alias.normalized_alias = hm.home_team_norm
         AND home_alias.team_id = hm.team_id_home
         AND home_alias.verified
        LEFT JOIN core.team_aliases away_alias
          ON away_alias.source_name = 'football-data.co.uk'
         AND away_alias.competition_code = hm.competition_code
         AND away_alias.normalized_alias = hm.away_team_norm
         AND away_alias.team_id = hm.team_id_away
         AND away_alias.verified
        WHERE hm.source_division IN ('FIN','NOR','SWE')
          AND (
            hm.team_id_home IS NULL OR hm.team_id_away IS NULL
            OR hm.team_id_home = hm.team_id_away
            OR home_alias.team_alias_id IS NULL
            OR away_alias.team_alias_id IS NULL
          )
        """,
    )
    odds_errors = scalar(
        connection,
        """
        SELECT count(*) FROM core.historical_odds ho
        JOIN core.historical_matches hm ON hm.hist_match_id = ho.hist_match_id
        WHERE hm.source_division IN ('FIN','NOR','SWE')
          AND (
            ho.market_type <> '1X2'
            OR NOT ho.is_closing
            OR ho.price_timing <> 'closing'
            OR ho.point_in_time_safe
            OR ho.line_value IS NOT NULL
            OR ho.odd_home IS NULL OR ho.odd_home <= 1
            OR ho.odd_draw IS NULL OR ho.odd_draw <= 1
            OR ho.odd_away IS NULL OR ho.odd_away <= 1
            OR ho.odd_over IS NOT NULL OR ho.odd_under IS NOT NULL
          )
        """,
    )
    timezone_errors = scalar(
        connection,
        """
        SELECT count(*) FROM core.historical_matches
        WHERE source_division IN ('FIN','NOR','SWE')
          AND match_time_local IS NOT NULL
          AND (
            source_timezone <> 'unknown'
            OR NOT (quality_flags ? 'source_time_timezone_unverified')
          )
        """,
    )
    partial_errors = scalar(
        connection,
        """
        SELECT count(*) FROM core.historical_matches
        WHERE source_division IN ('FIN','NOR','SWE')
          AND (
            (season_label = '2026' AND NOT (quality_flags ? 'partial_season_snapshot'))
            OR (season_label <> '2026' AND quality_flags ? 'partial_season_snapshot')
          )
        """,
    )
    future_matches = scalar(
        connection,
        """
        SELECT count(*) FROM core.historical_matches
        WHERE source_division IN ('FIN','NOR','SWE') AND match_date > current_date
        """,
    )
    metrics = {
        "duplicate_match_keys": duplicate_matches,
        "duplicate_source_rows": duplicate_rows,
        "duplicate_odds_keys": duplicate_odds,
        "result_errors": result_errors,
        "mapping_errors": mapping_errors,
        "odds_shape_timing_pit_errors": odds_errors,
        "timezone_flag_errors": timezone_errors,
        "partial_snapshot_flag_errors": partial_errors,
        "future_matches": future_matches,
    }
    if any(metrics.values()):
        audit.fail("global_constraints", "One or more scoped Nordic invariants failed.", metrics)
    audit.metrics["global_constraints"] = metrics

    counts = dict(
        connection.execute(
            """
            SELECT
              count(*) AS matches,
              count(*) FILTER (WHERE match_time_local IS NOT NULL) AS matches_with_time,
              count(*) FILTER (WHERE source_timezone = 'unknown') AS timezone_unknown,
              count(*) FILTER (WHERE quality_flags ? 'source_time_timezone_unverified') AS timezone_flagged,
              count(*) FILTER (WHERE quality_flags ? 'partial_season_snapshot') AS partial_2026,
              count(*) FILTER (WHERE result_status = 'unverified') AS unverified
            FROM core.historical_matches
            WHERE source_division IN ('FIN','NOR','SWE')
            """
        ).fetchone()
    )
    odds = scalar(
        connection,
        """
        SELECT count(*) FROM core.historical_odds ho
        JOIN core.historical_matches hm ON hm.hist_match_id = ho.hist_match_id
        WHERE hm.source_division IN ('FIN','NOR','SWE')
        """,
    )
    counts = {key: int(value) for key, value in counts.items()}
    counts["odds"] = odds
    audit.metrics["database_counts"] = counts
    expected_counts = {
        "matches": 9618,
        "matches_with_time": 9618,
        "timezone_unknown": 9618,
        "timezone_flagged": 9618,
        "partial_2026": 308,
        "unverified": 9618,
        "odds": 30022,
    }
    if counts != expected_counts:
        audit.fail("database_counts", "Nordic database counts differ from the frozen expected snapshot.", {
            "expected": expected_counts,
            "actual": counts,
        })


def audit_explicit_quarantine(connection, audit: Audit) -> None:
    pinnacle_after_cutoff = scalar(
        connection,
        """
        SELECT count(*) FROM core.historical_odds ho
        JOIN core.historical_matches hm ON hm.hist_match_id = ho.hist_match_id
        WHERE hm.source_division IN ('FIN','NOR','SWE')
          AND hm.match_date >= DATE '2025-07-23'
          AND ho.bookmaker = 'Pinnacle'
        """,
    )
    fin_anomaly_quotes = [
        dict(row)
        for row in connection.execute(
            """
            SELECT ho.bookmaker, ho.odd_home, ho.odd_draw, ho.odd_away
            FROM core.historical_odds ho
            JOIN core.historical_matches hm ON hm.hist_match_id = ho.hist_match_id
            WHERE hm.source_division = 'FIN'
              AND hm.match_date = DATE '2021-08-31'
              AND hm.home_team_norm = 'ktp'
              AND hm.away_team_norm = 'interturku'
              AND ho.bookmaker IN ('MarketMax','MarketAverage')
            """
        )
    ]
    raw_anomaly = connection.execute(
        """
        SELECT raw_row->>'MaxCH' AS max_h, raw_row->>'MaxCD' AS max_d,
               raw_row->>'MaxCA' AS max_a, raw_row->>'AvgCH' AS avg_h,
               raw_row->>'AvgCD' AS avg_d, raw_row->>'AvgCA' AS avg_a
        FROM core.historical_matches
        WHERE source_division = 'FIN'
          AND match_date = DATE '2021-08-31'
          AND home_team_norm = 'ktp'
          AND away_team_norm = 'interturku'
        """
    ).fetchone()
    expected_raw = {
        "max_h": "12",
        "max_d": "6.05",
        "max_a": "1.29",
        "avg_h": "10.91",
        "avg_d": "5.74",
        "avg_a": "1.75",
    }
    if pinnacle_after_cutoff or fin_anomaly_quotes or dict(raw_anomaly or {}) != expected_raw:
        audit.fail("explicit_quarantine", "Known risky Nordic quotes were not quarantined with raw evidence preserved.", {
            "pinnacle_after_cutoff_normalized": pinnacle_after_cutoff,
            "fin_max_avg_normalized": fin_anomaly_quotes,
            "fin_raw_evidence": dict(raw_anomaly or {}),
            "expected_raw_evidence": expected_raw,
        })
    audit.metrics["explicit_quarantine"] = {
        "pinnacle_groups_quarantined": 252,
        "fin_market_groups_quarantined": 2,
        "normalized_pinnacle_after_cutoff": pinnacle_after_cutoff,
        "normalized_fin_anomaly_max_avg": len(fin_anomaly_quotes),
        "raw_fin_anomaly_preserved": dict(raw_anomaly or {}) == expected_raw,
    }


def audit_idempotency(connection, audit: Audit) -> dict[str, Any]:
    rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT import_run_id, requested_files, imported_files,
                   matches_upserted, odds_upserted, status, error_summary, report
            FROM raw.historical_import_runs
            WHERE requested_files = 3
            ORDER BY import_run_id DESC
            LIMIT 2
            """
        )
    ]
    summaries: list[dict[str, Any]] = []
    fingerprints: list[str] = []
    for row in rows:
        files = (row.get("report") or {}).get("files") or []
        pinned = sorted(
            (
                item.get("division"),
                item.get("url"),
                item.get("sha256"),
                (item.get("validation") or {}).get("valid_matches"),
                (item.get("database") or {}).get("matches_upserted"),
                (item.get("database") or {}).get("odds_upserted"),
            )
            for item in files
        )
        fingerprint = hashlib.sha256(canonical_json(pinned).encode("utf-8")).hexdigest()
        fingerprints.append(fingerprint)
        summaries.append({
            "import_run_id": int(row["import_run_id"]),
            "requested_files": int(row["requested_files"]),
            "imported_files": int(row["imported_files"]),
            "matches_processed": int(row["matches_upserted"]),
            "odds_processed": int(row["odds_upserted"]),
            "status": row["status"],
            "error_summary": row["error_summary"],
            "reported_files": len(files),
            "pinned_input_fingerprint": fingerprint,
        })
    passed = (
        len(summaries) == 2
        and all(item["requested_files"] == 3 for item in summaries)
        and all(item["imported_files"] == 3 for item in summaries)
        and all(item["matches_processed"] == 9618 for item in summaries)
        and all(item["odds_processed"] == 30022 for item in summaries)
        and all(item["status"] == "succeeded" for item in summaries)
        and all(item["error_summary"] is None for item in summaries)
        and all(item["reported_files"] == 3 for item in summaries)
        and len(set(fingerprints)) == 1
    )
    result = {
        "status": "pass" if passed else "fail",
        "method": "two_consecutive_three-file_imports_with_identical_hash-pinned_inputs",
        "runs": summaries,
    }
    if not passed:
        audit.fail("idempotency", "Latest two Nordic imports are not count/input idempotent.", result)
    return result


def hash_query(connection, query: str, cursor_name: str) -> str:
    digest = hashlib.sha256()
    with connection.cursor(name=cursor_name) as cursor:
        cursor.execute(query)
        while True:
            rows = cursor.fetchmany(2000)
            if not rows:
                break
            for row in rows:
                digest.update(canonical_json(dict(row)).encode("utf-8"))
                digest.update(b"\n")
    return digest.hexdigest()


def logical_fingerprints(connection) -> dict[str, str]:
    return {
        "source_files": hash_query(
            connection,
            """
            SELECT source_name, source_url, source_division, season_label,
                   http_etag, http_last_modified, content_sha256, content_bytes,
                   storage_path, parser_version, row_count, status, validation_report
            FROM raw.source_files
            WHERE source_division IN ('FIN','NOR','SWE')
            ORDER BY source_division
            """,
            "nordic_hash_sources",
        ),
        "matches": hash_query(
            connection,
            """
            SELECT hm.competition_code, hm.source_division, hm.season_label,
                   hm.match_date, hm.match_time_local, hm.source_timezone,
                   hm.home_team, hm.away_team, hm.home_team_norm, hm.away_team_norm,
                   hm.home_goals, hm.away_goals, hm.full_time_result,
                   hm.result_status, hm.ht_home_goals, hm.ht_away_goals,
                   hm.half_time_result, hm.source_row_number, hm.source_row_hash,
                   hm.match_stats, hm.quality_flags, hm.raw_row,
                   ht.normalized_name AS mapped_home_norm, ht.country_code AS mapped_home_country,
                   at.normalized_name AS mapped_away_norm, at.country_code AS mapped_away_country,
                   sf.content_sha256 AS source_sha256
            FROM core.historical_matches hm
            JOIN raw.source_files sf ON sf.source_file_id = hm.source_file_id
            JOIN core.teams ht ON ht.team_id = hm.team_id_home
            JOIN core.teams at ON at.team_id = hm.team_id_away
            WHERE hm.source_division IN ('FIN','NOR','SWE')
            ORDER BY hm.source_division, hm.source_row_number
            """,
            "nordic_hash_matches",
        ),
        "odds": hash_query(
            connection,
            """
            SELECT hm.source_division, hm.source_row_number,
                   ho.bookmaker, ho.market_type, ho.is_closing,
                   ho.price_timing, ho.point_in_time_safe,
                   ho.line_value, ho.odd_home, ho.odd_draw, ho.odd_away,
                   ho.odd_over, ho.odd_under, ho.source_columns, ho.quality_flags
            FROM core.historical_odds ho
            JOIN core.historical_matches hm ON hm.hist_match_id = ho.hist_match_id
            WHERE hm.source_division IN ('FIN','NOR','SWE')
            ORDER BY hm.source_division, hm.source_row_number, ho.bookmaker
            """,
            "nordic_hash_odds",
        ),
        "team_aliases": hash_query(
            connection,
            """
            SELECT a.source_name, a.competition_code, a.source_alias,
                   a.normalized_alias, a.verified, a.verification_method,
                   a.evidence, t.canonical_name, t.normalized_name, t.country_code
            FROM core.team_aliases a
            JOIN core.teams t ON t.team_id = a.team_id
            WHERE a.competition_code IN ('FI1','NO1','SE1')
            ORDER BY a.competition_code, a.normalized_alias
            """,
            "nordic_hash_aliases",
        ),
    }


def add_policy_warnings(audit: Audit) -> None:
    audit.warn(
        "single_source_results",
        "All 9,618 results are exact and hash-pinned to the provider CSVs, but have not been score-verified against a second results provider.",
        {"unverified_matches": 9618},
    )
    audit.warn(
        "timezone_unknown",
        "The provider does not document the Time timezone. All 9,618 timed matches remain timezone=unknown and carry an explicit quality flag.",
        {"flagged_matches": 9618},
    )
    audit.warn(
        "partial_2026_snapshot",
        "The 2026 calendar seasons are live partial snapshots, not completed seasons.",
        {"flagged_matches": 308},
    )
    audit.warn(
        "quarantined_source_odds",
        "252 post-warning Pinnacle groups and 2 structurally inconsistent FIN Max/Avg groups remain only in raw source rows and were not normalized.",
        {"pinnacle_groups": 252, "fin_max_avg_groups": 2},
    )
    audit.warn(
        "closing_odds_not_point_in_time",
        "All 30,022 normalized Nordic quotes are closing labels without per-row capture timestamps and are correctly point_in_time_safe=false.",
        {"closing_quotes": 30022},
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = parse_args(argv)
    started = datetime.now(UTC)
    audit = Audit()
    source_report, source_report_sha = load_source_report(args.source_report, audit)

    with connect() as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        audit_database(connection, audit, source_report)
        audit_global_constraints(connection, audit)
        audit_explicit_quarantine(connection, audit)
        idempotency = audit_idempotency(connection, audit)
        fingerprints = logical_fingerprints(connection)

    reconstruction = audit.metrics.get("reconstruction") or {}
    if reconstruction.get("matches") != 9618 or reconstruction.get("odds") != 30022:
        audit.fail("reconstruction_totals", "Independent reconstruction totals differ from the frozen database scope.", {
            "actual": reconstruction,
            "expected": {"matches": 9618, "odds": 30022},
        })
    if reconstruction.get("pinnacle_quarantined") != 252:
        audit.fail("reconstruction_pinnacle_quarantine", "Independent Pinnacle quarantine total is not 252.", {
            "actual": reconstruction.get("pinnacle_quarantined"),
        })
    if reconstruction.get("market_consistency_quarantined") != 2:
        audit.fail("reconstruction_market_quarantine", "Independent market quarantine total is not 2.", {
            "actual": reconstruction.get("market_consistency_quarantined"),
        })

    add_policy_warnings(audit)
    fatal_codes = {item.code for item in audit.findings if item.severity == "fatal"}
    warning_codes = {item.code for item in audit.findings if item.severity == "warning"}
    fatal_count = sum(count for code, count in audit.occurrences.items() if code in fatal_codes)
    warning_count = sum(count for code, count in audit.occurrences.items() if code in warning_codes)
    status = "fail" if fatal_count else "pass_with_warnings" if warning_count else "pass"
    report = {
        "audit_version": AUDIT_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "duration_seconds": round((datetime.now(UTC) - started).total_seconds(), 3),
        "status": status,
        "scope": {
            "source": "football-data.co.uk",
            "divisions": list(DIVISIONS),
            "competitions": [DIVISIONS[key]["competition"] for key in DIVISIONS],
            "calendar_seasons": ["2012", "2013", "2014", "2015", "2016", "2017",
                                 "2018", "2019", "2020", "2021", "2022", "2023",
                                 "2024", "2025", "2026"],
        },
        "summary": {
            "fatal_findings": fatal_count,
            "warning_findings": warning_count,
            "all_source_hashes_matched": not any(
                item.code in {"source_file_missing", "source_provenance"}
                for item in audit.findings
            ),
            "all_matches_reconstructed": reconstruction.get("matches") == 9618,
            "all_odds_reconstructed": reconstruction.get("odds") == 30022,
            "normalized_risky_quotes": (
                audit.metrics.get("explicit_quarantine", {}).get("normalized_pinnacle_after_cutoff", -1)
                + audit.metrics.get("explicit_quarantine", {}).get("normalized_fin_anomaly_max_avg", -1)
            ),
        },
        "source_report_sha256": source_report_sha,
        "metrics": audit.metrics,
        "idempotency": idempotency,
        "logical_fingerprints": fingerprints,
        "finding_occurrences": dict(sorted(audit.occurrences.items())),
        "findings": [asdict(item) for item in audit.findings],
    }
    rendered = json.dumps(canonicalize(report), ensure_ascii=False, indent=2)
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(json.dumps({
        "status": status,
        "summary": report["summary"],
        "database_counts": audit.metrics.get("database_counts"),
        "reconstruction": reconstruction,
        "idempotency": idempotency,
        "output": str(args.output.resolve()) if args.output else None,
    }, ensure_ascii=False, indent=2))
    return 2 if status == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
