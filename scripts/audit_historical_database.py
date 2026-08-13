#!/usr/bin/env python3
"""Independently audit the persisted football-data.co.uk historical dataset.

The audit is intentionally separate from the importer. It reads PostgreSQL and
the hash-pinned CSV files, reconstructs every normalized match and odds quote,
and compares the reconstruction with the stored rows. It never updates the
database.

Typical use:

    backend/.venv/Scripts/python.exe scripts/audit_historical_database.py \
      --output output/historical-database-audit.json

To prove an offline re-import is idempotent, keep the first report and compare
the logical fingerprints after the re-import:

    backend/.venv/Scripts/python.exe scripts/audit_historical_database.py \
      --baseline-report output/historical-database-audit-before.json \
      --output output/historical-database-audit.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import connect  # noqa: E402


AUDIT_VERSION = "historical-database-audit-v1"
SOURCE_NAME = "football-data.co.uk"
EXPECTED_SEASONS = tuple(f"{year}/{year + 1}" for year in range(1993, 2026))
SEASON_SOURCE_CODES = {
    f"{year}/{year + 1}": f"{year % 100:02d}{(year + 1) % 100:02d}"
    for year in range(1993, 2026)
}
DIVISIONS = {
    "E0": {"competition": "PL", "country": "GB", "timezone": "Europe/London"},
    "SP1": {"competition": "PD", "country": "ES", "timezone": "Europe/London"},
    "D1": {"competition": "BL1", "country": "DE", "timezone": "Europe/London"},
    "I1": {"competition": "SA", "country": "IT", "timezone": "Europe/London"},
    "F1": {"competition": "FL1", "country": "FR", "timezone": "Europe/London"},
}
EXPECTED_TEAMS: dict[tuple[str, str], int] = {}
for _season in EXPECTED_SEASONS:
    _start_year = int(_season[:4])
    EXPECTED_TEAMS[("PL", _season)] = 22 if _start_year <= 1994 else 20
    EXPECTED_TEAMS[("PD", _season)] = 22 if _start_year in {1995, 1996} else 20
    EXPECTED_TEAMS[("BL1", _season)] = 18
    EXPECTED_TEAMS[("SA", _season)] = 18 if _start_year <= 2003 else 20
    EXPECTED_TEAMS[("FL1", _season)] = (
        18 if 1997 <= _start_year <= 2001 or _start_year >= 2023 else 20
    )

# Ligue 1's 2019/20 season was permanently stopped after 279 matches. It is an
# authentic incomplete schedule, not a missing-ingestion defect.
SCHEDULE_EXCEPTIONS = {
    ("FL1", "2019/2020"): {
        "expected_matches": 279,
        "reason": "season permanently curtailed; do not invent the unplayed fixtures",
    },
}
MATCH_STAT_COLUMNS = (
    "Referee",
    "HS",
    "AS",
    "HST",
    "AST",
    "HF",
    "AF",
    "HC",
    "AC",
    "HY",
    "AY",
    "HR",
    "AR",
)


@dataclass(frozen=True)
class OddsSpec:
    bookmaker: str
    market_type: str
    is_closing: bool
    columns: dict[str, str]
    line_column: str | None = None


ODDS_SPECS = (
    OddsSpec("Betfair", "1X2", False, {"odd_home": "BFH", "odd_draw": "BFD", "odd_away": "BFA"}),
    OddsSpec("Betfred", "1X2", False, {"odd_home": "BFDH", "odd_draw": "BFDD", "odd_away": "BFDA"}),
    OddsSpec("BetMGM", "1X2", False, {"odd_home": "BMGMH", "odd_draw": "BMGMD", "odd_away": "BMGMA"}),
    OddsSpec("BetVictor", "1X2", False, {"odd_home": "BVH", "odd_draw": "BVD", "odd_away": "BVA"}),
    OddsSpec("Blue Square", "1X2", False, {"odd_home": "BSH", "odd_draw": "BSD", "odd_away": "BSA"}),
    OddsSpec("Bet&Win", "1X2", False, {"odd_home": "BWH", "odd_draw": "BWD", "odd_away": "BWA"}),
    OddsSpec("Coral", "1X2", False, {"odd_home": "CLH", "odd_draw": "CLD", "odd_away": "CLA"}),
    OddsSpec("Gamebookers", "1X2", False, {"odd_home": "GBH", "odd_draw": "GBD", "odd_away": "GBA"}),
    OddsSpec("Interwetten", "1X2", False, {"odd_home": "IWH", "odd_draw": "IWD", "odd_away": "IWA"}),
    OddsSpec("Ladbrokes", "1X2", False, {"odd_home": "LBH", "odd_draw": "LBD", "odd_away": "LBA"}),
    OddsSpec("PinnacleLegacy", "1X2", False, {"odd_home": "PH", "odd_draw": "PD", "odd_away": "PA"}),
    OddsSpec("Sporting Odds", "1X2", False, {"odd_home": "SOH", "odd_draw": "SOD", "odd_away": "SOA"}),
    OddsSpec("Sportingbet", "1X2", False, {"odd_home": "SBH", "odd_draw": "SBD", "odd_away": "SBA"}),
    OddsSpec("Stan James", "1X2", False, {"odd_home": "SJH", "odd_draw": "SJD", "odd_away": "SJA"}),
    OddsSpec("Stanleybet", "1X2", False, {"odd_home": "SYH", "odd_draw": "SYD", "odd_away": "SYA"}),
    OddsSpec("VC Bet", "1X2", False, {"odd_home": "VCH", "odd_draw": "VCD", "odd_away": "VCA"}),
    OddsSpec("William Hill", "1X2", False, {"odd_home": "WHH", "odd_draw": "WHD", "odd_away": "WHA"}),
    OddsSpec("BetbrainMax", "1X2", False, {"odd_home": "BbMxH", "odd_draw": "BbMxD", "odd_away": "BbMxA"}),
    OddsSpec("BetbrainAverage", "1X2", False, {"odd_home": "BbAvH", "odd_draw": "BbAvD", "odd_away": "BbAvA"}),
    OddsSpec("Bet365", "1X2", False, {"odd_home": "B365H", "odd_draw": "B365D", "odd_away": "B365A"}),
    OddsSpec("Pinnacle", "1X2", False, {"odd_home": "PSH", "odd_draw": "PSD", "odd_away": "PSA"}),
    OddsSpec("MarketMax", "1X2", False, {"odd_home": "MaxH", "odd_draw": "MaxD", "odd_away": "MaxA"}),
    OddsSpec("MarketAverage", "1X2", False, {"odd_home": "AvgH", "odd_draw": "AvgD", "odd_away": "AvgA"}),
    OddsSpec("BetfairExchange", "1X2", False, {"odd_home": "BFEH", "odd_draw": "BFED", "odd_away": "BFEA"}),
    OddsSpec("Bet365", "1X2", True, {"odd_home": "B365CH", "odd_draw": "B365CD", "odd_away": "B365CA"}),
    OddsSpec("Pinnacle", "1X2", True, {"odd_home": "PSCH", "odd_draw": "PSCD", "odd_away": "PSCA"}),
    OddsSpec("MarketMax", "1X2", True, {"odd_home": "MaxCH", "odd_draw": "MaxCD", "odd_away": "MaxCA"}),
    OddsSpec("MarketAverage", "1X2", True, {"odd_home": "AvgCH", "odd_draw": "AvgCD", "odd_away": "AvgCA"}),
    OddsSpec("BetfairExchange", "1X2", True, {"odd_home": "BFECH", "odd_draw": "BFECD", "odd_away": "BFECA"}),
    OddsSpec("Bet365", "OU25", False, {"odd_over": "B365>2.5", "odd_under": "B365<2.5"}),
    OddsSpec("Pinnacle", "OU25", False, {"odd_over": "P>2.5", "odd_under": "P<2.5"}),
    OddsSpec("MarketMax", "OU25", False, {"odd_over": "Max>2.5", "odd_under": "Max<2.5"}),
    OddsSpec("MarketAverage", "OU25", False, {"odd_over": "Avg>2.5", "odd_under": "Avg<2.5"}),
    OddsSpec("BetfairExchange", "OU25", False, {"odd_over": "BFE>2.5", "odd_under": "BFE<2.5"}),
    OddsSpec("Bet365", "OU25", True, {"odd_over": "B365C>2.5", "odd_under": "B365C<2.5"}),
    OddsSpec("Pinnacle", "OU25", True, {"odd_over": "PC>2.5", "odd_under": "PC<2.5"}),
    OddsSpec("MarketMax", "OU25", True, {"odd_over": "MaxC>2.5", "odd_under": "MaxC<2.5"}),
    OddsSpec("MarketAverage", "OU25", True, {"odd_over": "AvgC>2.5", "odd_under": "AvgC<2.5"}),
    OddsSpec("BetfairExchange", "OU25", True, {"odd_over": "BFEC>2.5", "odd_under": "BFEC<2.5"}),
    OddsSpec("Gamebookers", "OU25", False, {"odd_over": "GB>2.5", "odd_under": "GB<2.5"}),
    OddsSpec("BetbrainMax", "OU25", False, {"odd_over": "BbMx>2.5", "odd_under": "BbMx<2.5"}),
    OddsSpec("BetbrainAverage", "OU25", False, {"odd_over": "BbAv>2.5", "odd_under": "BbAv<2.5"}),
    OddsSpec("Bet365", "AH", False, {"odd_home": "B365AHH", "odd_away": "B365AHA"}, "AHh"),
    OddsSpec("Pinnacle", "AH", False, {"odd_home": "PAHH", "odd_away": "PAHA"}, "AHh"),
    OddsSpec("MarketMax", "AH", False, {"odd_home": "MaxAHH", "odd_away": "MaxAHA"}, "AHh"),
    OddsSpec("MarketAverage", "AH", False, {"odd_home": "AvgAHH", "odd_away": "AvgAHA"}, "AHh"),
    OddsSpec("BetfairExchange", "AH", False, {"odd_home": "BFEAHH", "odd_away": "BFEAHA"}, "AHh"),
    OddsSpec("Bet365", "AH", True, {"odd_home": "B365CAHH", "odd_away": "B365CAHA"}, "AHCh"),
    OddsSpec("Pinnacle", "AH", True, {"odd_home": "PCAHH", "odd_away": "PCAHA"}, "AHCh"),
    OddsSpec("MarketMax", "AH", True, {"odd_home": "MaxCAHH", "odd_away": "MaxCAHA"}, "AHCh"),
    OddsSpec("MarketAverage", "AH", True, {"odd_home": "AvgCAHH", "odd_away": "AvgCAHA"}, "AHCh"),
    OddsSpec("BetfairExchange", "AH", True, {"odd_home": "BFECAHH", "odd_away": "BFECAHA"}, "AHCh"),
    OddsSpec("Gamebookers", "AH", False, {"odd_home": "GBAHH", "odd_away": "GBAHA"}, "GBAH"),
    OddsSpec("Ladbrokes", "AH", False, {"odd_home": "LBAHH", "odd_away": "LBAHA"}, "LBAH"),
    OddsSpec("BetbrainMax", "AH", False, {"odd_home": "BbMxAHH", "odd_away": "BbMxAHA"}, "BbAHh"),
    OddsSpec("BetbrainAverage", "AH", False, {"odd_home": "BbAvAHH", "odd_away": "BbAvAHA"}, "BbAHh"),
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
        self.metrics: dict[str, Any] = {}
        self._sample_counts: Counter[str] = Counter()

    def add(
        self,
        severity: str,
        code: str,
        message: str,
        evidence: dict[str, Any] | None = None,
        *,
        sample_limit: int = 12,
    ) -> None:
        self._sample_counts[code] += 1
        if self._sample_counts[code] <= sample_limit:
            self.findings.append(Finding(severity, code, message, evidence or {}))

    def fail(self, code: str, message: str, evidence: dict[str, Any] | None = None) -> None:
        self.add("fatal", code, message, evidence)

    def warn(self, code: str, message: str, evidence: dict[str, Any] | None = None) -> None:
        self.add("warning", code, message, evidence)

    @property
    def fatal_count(self) -> int:
        codes = {item.code for item in self.findings if item.severity == "fatal"}
        return sum(count for code, count in self._sample_counts.items() if code in codes)

    @property
    def warning_count(self) -> int:
        codes = {item.code for item in self.findings if item.severity == "warning"}
        return sum(count for code, count in self._sample_counts.items() if code in codes)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit PostgreSQL historical matches/odds against every hash-pinned source CSV."
    )
    parser.add_argument("--output", type=Path, help="Write the complete JSON report to this path.")
    parser.add_argument(
        "--baseline-report",
        type=Path,
        help="Compare counts and logical fingerprints with an earlier report after a repeated import.",
    )
    return parser.parse_args(argv)


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


def parse_match_date(value: str) -> date:
    for pattern in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            continue
    raise ValueError(f"invalid date {value!r}")


def parse_match_time(value: str) -> time | None:
    raw = value.strip()
    if not raw:
        return None
    for pattern in ("%H:%M", "%H.%M"):
        try:
            return datetime.strptime(raw, pattern).time()
        except ValueError:
            continue
    raise ValueError(f"invalid time {value!r}")


def parse_decimal(value: str, *, is_odd: bool) -> Decimal | None:
    raw = value.strip()
    if not raw:
        return None
    try:
        result = Decimal(raw)
    except InvalidOperation:
        return None
    if not result.is_finite() or (is_odd and result <= 1):
        return None
    return result


def canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    if isinstance(value, Decimal):
        if value == 0:
            return "0"
        return format(value.normalize(), "f")
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(canonicalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def csv_rows(path: Path) -> tuple[dict[int, dict[str, str]], str, list[str]]:
    text, encoding = decode_csv(path.read_bytes())
    reader = csv.DictReader(text.splitlines())
    if reader.fieldnames is None:
        raise ValueError("missing CSV header")
    rows: dict[int, dict[str, str]] = {}
    for row_number, source_row in enumerate(reader, start=2):
        row = {
            str(key).strip(): (value or "").strip()
            for key, value in source_row.items()
            if key
        }
        if any(row.values()):
            rows[row_number] = row
    return rows, encoding, [str(item).strip() for item in reader.fieldnames if item]


def row_sha256(row: dict[str, str]) -> str:
    return hashlib.sha256(canonical_json(row).encode("utf-8")).hexdigest()


def expected_odds(row: dict[str, str], season_label: str) -> tuple[dict[tuple[str, str, bool], dict[str, Any]], int]:
    quotes: dict[tuple[str, str, bool], dict[str, Any]] = {}
    invalid_quote_count = 0
    start_year = int(season_label[:4])
    for spec in ODDS_SPECS:
        raw_values = {target: row.get(column, "") for target, column in spec.columns.items()}
        if not any(value.strip() for value in raw_values.values()):
            continue
        parsed = {target: parse_decimal(value, is_odd=True) for target, value in raw_values.items()}
        if any(value is None for value in parsed.values()):
            invalid_quote_count += 1
            continue
        line_column = spec.line_column
        if (
            spec.bookmaker == "Bet365"
            and spec.market_type == "AH"
            and line_column == "AHh"
            and not row.get("AHh", "").strip()
            and row.get("B365AH", "").strip()
        ):
            line_column = "B365AH"
        line_value = parse_decimal(row.get(line_column, ""), is_odd=False) if line_column else None
        if line_column and line_value is None:
            invalid_quote_count += 1
            continue
        columns: Iterable[str]
        if line_column:
            columns = (*spec.columns.values(), line_column)
        else:
            columns = spec.columns.values()
        key = (spec.bookmaker, spec.market_type, spec.is_closing)
        quotes[key] = {
            "price_timing": (
                "closing"
                if spec.is_closing
                else "opening"
                if start_year >= 2019
                else "preclosing_legacy"
            ),
            "point_in_time_safe": False,
            "line_value": line_value,
            "odd_home": parsed.get("odd_home"),
            "odd_draw": parsed.get("odd_draw"),
            "odd_away": parsed.get("odd_away"),
            "odd_over": parsed.get("odd_over"),
            "odd_under": parsed.get("odd_under"),
            "source_columns": {column: row.get(column, "") for column in columns},
            "quality_flags": (
                ["provider_warned_stale_after_2025-07-23"]
                if season_label == "2025/2026" and spec.bookmaker == "Pinnacle"
                else []
            ),
        }
    return quotes, invalid_quote_count


def invalid_odds_cells(row: dict[str, str]) -> list[dict[str, str]]:
    line_or_count_columns = {
        "AHh",
        "AHCh",
        "BbAHh",
        "GBAH",
        "LBAH",
        "B365AH",
        "Bb1X2",
        "BbOU",
        "BbAH",
    }
    issues: list[dict[str, str]] = []
    for column in row:
        if column in line_or_count_columns:
            continue
        looks_like_price = (
            column.endswith((">2.5", "<2.5"))
            or re.fullmatch(r"[A-Za-z0-9&.]+(?:AHH|AHA|H|D|A)", column) is not None
        )
        if not looks_like_price:
            continue
        raw = row.get(column, "").strip()
        if not raw:
            continue
        try:
            value = Decimal(raw)
        except InvalidOperation:
            continue
        if value.is_finite() and value <= 1:
            issues.append({"column": column, "value": raw})
    return issues


def expected_match_fields(
    source: dict[str, Any],
    row_number: int,
    row: dict[str, str],
) -> dict[str, Any]:
    division = source["source_division"]
    home_goals = int(row["FTHG"])
    away_goals = int(row["FTAG"])
    full_time_result = "H" if home_goals > away_goals else "A" if home_goals < away_goals else "D"
    ht_home = int(row["HTHG"]) if row.get("HTHG", "").strip() else None
    ht_away = int(row["HTAG"]) if row.get("HTAG", "").strip() else None
    if (ht_home is None) != (ht_away is None):
        raise ValueError("only one half-time score is present")
    half_time_result = None
    if ht_home is not None and ht_away is not None:
        half_time_result = "H" if ht_home > ht_away else "A" if ht_home < ht_away else "D"
    return {
        "competition_code": DIVISIONS[division]["competition"],
        "source_division": division,
        "season_label": source["season_label"],
        "match_date": parse_match_date(row["Date"]),
        "match_time_local": parse_match_time(row.get("Time", "")),
        "source_timezone": DIVISIONS[division]["timezone"],
        "home_team": row["HomeTeam"],
        "away_team": row["AwayTeam"],
        "home_team_norm": normalize_team_name(row["HomeTeam"]),
        "away_team_norm": normalize_team_name(row["AwayTeam"]),
        "home_goals": home_goals,
        "away_goals": away_goals,
        "full_time_result": full_time_result,
        "ht_home_goals": ht_home,
        "ht_away_goals": ht_away,
        "half_time_result": half_time_result,
        "source_row_number": row_number,
        "source_row_hash": row_sha256(row),
        "match_stats": {
            key: row[key]
            for key in MATCH_STAT_COLUMNS
            if row.get(key, "") != ""
        },
        "raw_row": row,
    }


def compare_values(
    audit: Audit,
    code: str,
    context: dict[str, Any],
    expected: dict[str, Any],
    actual: dict[str, Any],
    fields: Iterable[str],
) -> None:
    differences = {
        field: {
            "expected": canonicalize(expected.get(field)),
            "actual": canonicalize(actual.get(field)),
        }
        for field in fields
        if canonicalize(expected.get(field)) != canonicalize(actual.get(field))
    }
    if differences:
        audit.fail(code, "Persisted values do not match the hash-pinned CSV row.", {
            **context,
            "differences": differences,
        })


def query_count(connection, query: str, params: tuple[Any, ...] = ()) -> int:
    row = connection.execute(query, params).fetchone()
    return int(next(iter(row.values())))


def audit_global_constraints(connection, audit: Audit) -> None:
    duplicate_matches = query_count(
        connection,
        """
        SELECT count(*) FROM (
            SELECT source_division, season_label, match_date, home_team_norm, away_team_norm
            FROM core.historical_matches
            WHERE source_division IN ('E0','SP1','D1','I1','F1')
            GROUP BY 1,2,3,4,5 HAVING count(*) > 1
        ) duplicates
        """,
    )
    duplicate_source_rows = query_count(
        connection,
        """
        SELECT count(*) FROM (
            SELECT source_file_id, source_row_number
            FROM core.historical_matches
            WHERE source_division IN ('E0','SP1','D1','I1','F1')
            GROUP BY 1,2 HAVING count(*) > 1
        ) duplicates
        """,
    )
    duplicate_odds = query_count(
        connection,
        """
        SELECT count(*) FROM (
            SELECT ho.hist_match_id, ho.bookmaker, ho.market_type, ho.is_closing
            FROM core.historical_odds ho
            JOIN core.historical_matches hm ON hm.hist_match_id = ho.hist_match_id
            WHERE hm.source_division IN ('E0','SP1','D1','I1','F1')
            GROUP BY 1,2,3,4 HAVING count(*) > 1
        ) duplicates
        """,
    )
    if duplicate_matches or duplicate_source_rows or duplicate_odds:
        audit.fail("duplicate_rows", "Duplicate normalized rows exist.", {
            "match_natural_keys": duplicate_matches,
            "source_rows": duplicate_source_rows,
            "odds_keys": duplicate_odds,
        })

    result_mismatches = query_count(
        connection,
        """
        SELECT count(*) FROM core.historical_matches
        WHERE source_division IN ('E0','SP1','D1','I1','F1')
          AND full_time_result <> CASE
            WHEN home_goals > away_goals THEN 'H'
            WHEN home_goals < away_goals THEN 'A'
            ELSE 'D'
        END
        """,
    )
    half_time_mismatches = query_count(
        connection,
        """
        SELECT count(*) FROM core.historical_matches
        WHERE source_division IN ('E0','SP1','D1','I1','F1')
          AND (
            ((ht_home_goals IS NULL OR ht_away_goals IS NULL) AND half_time_result IS NOT NULL)
            OR ((ht_home_goals IS NULL) <> (ht_away_goals IS NULL))
            OR (
            ht_home_goals IS NOT NULL AND
            half_time_result <> CASE
              WHEN ht_home_goals > ht_away_goals THEN 'H'
              WHEN ht_home_goals < ht_away_goals THEN 'A'
              ELSE 'D'
            END
            )
          )
        """,
    )
    half_exceeds_final = query_count(
        connection,
        """
        SELECT count(*) FROM core.historical_matches
        WHERE source_division IN ('E0','SP1','D1','I1','F1')
          AND result_status <> 'awarded'
          AND (ht_home_goals > home_goals OR ht_away_goals > away_goals)
        """,
    )
    if result_mismatches or half_time_mismatches or half_exceeds_final:
        audit.fail("score_semantics", "Stored score/result semantics are inconsistent.", {
            "full_time_result_mismatches": result_mismatches,
            "half_time_result_or_null_mismatches": half_time_mismatches,
            "non_awarded_half_time_exceeds_final": half_exceeds_final,
        })

    invalid_odds = query_count(
        connection,
        """
        SELECT count(*) FROM core.historical_odds ho
        JOIN core.historical_matches hm ON hm.hist_match_id = ho.hist_match_id
        WHERE hm.source_division IN ('E0','SP1','D1','I1','F1')
          AND (
              coalesce(odd_home <= 1, false)
           OR coalesce(odd_draw <= 1, false)
           OR coalesce(odd_away <= 1, false)
           OR coalesce(odd_over <= 1, false)
           OR coalesce(odd_under <= 1, false)
          )
        """,
    )
    invalid_shapes = query_count(
        connection,
        """
        SELECT count(*) FROM core.historical_odds ho
        JOIN core.historical_matches hm ON hm.hist_match_id = ho.hist_match_id
        WHERE hm.source_division IN ('E0','SP1','D1','I1','F1')
          AND (
          (market_type = '1X2' AND (
            odd_home IS NULL OR odd_draw IS NULL OR odd_away IS NULL
            OR odd_over IS NOT NULL OR odd_under IS NOT NULL OR line_value IS NOT NULL
          ))
          OR (market_type = 'OU25' AND (
            odd_over IS NULL OR odd_under IS NULL
            OR odd_home IS NOT NULL OR odd_draw IS NOT NULL OR odd_away IS NOT NULL
            OR line_value IS NOT NULL
          ))
          OR (market_type = 'AH' AND (
            line_value IS NULL OR odd_home IS NULL OR odd_away IS NULL
            OR odd_draw IS NOT NULL OR odd_over IS NOT NULL OR odd_under IS NOT NULL
          ))
          )
        """,
    )
    invalid_timing = query_count(
        connection,
        """
        SELECT count(*) FROM core.historical_odds ho
        JOIN core.historical_matches hm ON hm.hist_match_id = ho.hist_match_id
        WHERE hm.source_division IN ('E0','SP1','D1','I1','F1')
          AND (
            ho.point_in_time_safe
            OR (ho.is_closing AND ho.price_timing <> 'closing')
            OR (
             NOT ho.is_closing
             AND substring(hm.season_label from 1 for 4)::integer < 2019
             AND ho.price_timing <> 'preclosing_legacy'
           )
            OR (
             NOT ho.is_closing
             AND substring(hm.season_label from 1 for 4)::integer >= 2019
             AND ho.price_timing <> 'opening'
            )
          )
        """,
    )
    if invalid_odds or invalid_shapes or invalid_timing:
        audit.fail("invalid_normalized_odds", "Normalized odds contain illegal values or market shapes.", {
            "prices_at_or_below_one": invalid_odds,
            "market_shape_mismatches": invalid_shapes,
            "price_timing_or_point_in_time_mismatches": invalid_timing,
        })

    mapping_issues = connection.execute(
        """
        SELECT
          count(*) FILTER (WHERE hm.team_id_home IS NULL OR hm.team_id_away IS NULL) AS null_ids,
          count(*) FILTER (WHERE hm.team_id_home = hm.team_id_away) AS identical_ids,
          count(*) FILTER (
            WHERE ht.normalized_name <> hm.home_team_norm
               OR at.normalized_name <> hm.away_team_norm
          ) AS normalized_name_mismatches
        FROM core.historical_matches hm
        LEFT JOIN core.teams ht ON ht.team_id = hm.team_id_home
        LEFT JOIN core.teams at ON at.team_id = hm.team_id_away
        WHERE hm.source_division IN ('E0','SP1','D1','I1','F1')
        """
    ).fetchone()
    missing_aliases = query_count(
        connection,
        """
        SELECT count(*) FROM (
          SELECT hm.hist_match_id
          FROM core.historical_matches hm
          LEFT JOIN core.team_aliases a
            ON a.source_name = 'football-data.co.uk'
           AND a.competition_code = hm.competition_code
           AND a.normalized_alias = hm.home_team_norm
           AND a.team_id = hm.team_id_home
           AND a.verified
          WHERE hm.source_division IN ('E0','SP1','D1','I1','F1')
            AND a.team_alias_id IS NULL
          UNION ALL
          SELECT hm.hist_match_id
          FROM core.historical_matches hm
          LEFT JOIN core.team_aliases a
            ON a.source_name = 'football-data.co.uk'
           AND a.competition_code = hm.competition_code
           AND a.normalized_alias = hm.away_team_norm
           AND a.team_id = hm.team_id_away
           AND a.verified
          WHERE hm.source_division IN ('E0','SP1','D1','I1','F1')
            AND a.team_alias_id IS NULL
        ) missing
        """,
    )
    multi_team_mappings = query_count(
        connection,
        """
        SELECT count(*) FROM (
          SELECT competition_code, normalized_name
          FROM (
            SELECT competition_code, home_team_norm AS normalized_name, team_id_home AS team_id
            FROM core.historical_matches
            WHERE source_division IN ('E0','SP1','D1','I1','F1')
            UNION ALL
            SELECT competition_code, away_team_norm, team_id_away
            FROM core.historical_matches
            WHERE source_division IN ('E0','SP1','D1','I1','F1')
          ) appearances
          GROUP BY 1,2
          HAVING count(DISTINCT team_id) <> 1
        ) conflicts
        """,
    )
    if (
        int(mapping_issues["null_ids"])
        or int(mapping_issues["identical_ids"])
        or int(mapping_issues["normalized_name_mismatches"])
        or missing_aliases
        or multi_team_mappings
    ):
        audit.fail("team_mapping", "Historical team mappings are incomplete or inconsistent.", {
            **{key: int(value) for key, value in mapping_issues.items()},
            "missing_verified_alias_appearances": missing_aliases,
            "source_aliases_mapping_to_multiple_team_ids": multi_team_mappings,
        })

    audit.metrics["global_constraints"] = {
        "duplicate_match_keys": duplicate_matches,
        "duplicate_source_rows": duplicate_source_rows,
        "duplicate_odds_keys": duplicate_odds,
        "score_semantic_errors": result_mismatches + half_time_mismatches + half_exceeds_final,
        "invalid_normalized_odds": invalid_odds,
        "invalid_market_shapes": invalid_shapes,
        "invalid_price_timing_or_point_in_time": invalid_timing,
        "missing_verified_alias_appearances": missing_aliases,
    }


def audit_schedule_coverage(connection, audit: Audit) -> None:
    rows = connection.execute(
        """
        SELECT competition_code, season_label, count(*) AS matches,
               count(DISTINCT home_team_norm) AS home_teams,
               count(DISTINCT away_team_norm) AS away_teams,
               min(match_date) AS first_match_date,
               max(match_date) AS last_match_date
        FROM core.historical_matches
        WHERE source_division IN ('E0','SP1','D1','I1','F1')
        GROUP BY 1,2 ORDER BY 2,1
        """
    ).fetchall()
    actual_combinations = {(row["competition_code"], row["season_label"]) for row in rows}
    expected_combinations = set(EXPECTED_TEAMS)
    if actual_combinations != expected_combinations:
        audit.fail("coverage_scope", "Database league/season scope differs from the expected five-league, 33-season matrix.", {
            "missing": sorted(expected_combinations - actual_combinations),
            "unexpected": sorted(actual_combinations - expected_combinations),
        })

    coverage: list[dict[str, Any]] = []
    for row in rows:
        key = (row["competition_code"], row["season_label"])
        expected_teams = EXPECTED_TEAMS.get(key)
        exception = SCHEDULE_EXCEPTIONS.get(key)
        expected_matches = (
            exception["expected_matches"]
            if exception
            else expected_teams * (expected_teams - 1)
            if expected_teams
            else None
        )
        item = {
            **dict(row),
            "expected_teams": expected_teams,
            "expected_matches": expected_matches,
            "schedule_exception": exception,
        }
        coverage.append(canonicalize(item))
        if (
            expected_teams is None
            or int(row["matches"]) != expected_matches
            or int(row["home_teams"]) != expected_teams
            or int(row["away_teams"]) != expected_teams
        ):
            audit.fail("coverage_count", "League-season is not a complete double round-robin by count.", canonicalize(item))

    pair_errors = query_count(
        connection,
        """
        SELECT count(*) FROM (
          SELECT competition_code, season_label,
                 least(home_team_norm, away_team_norm) AS team_a,
                 greatest(home_team_norm, away_team_norm) AS team_b,
                 count(*) AS games,
                 count(*) FILTER (WHERE home_team_norm < away_team_norm) AS orientation_a_home,
                 count(*) FILTER (WHERE home_team_norm > away_team_norm) AS orientation_b_home
          FROM core.historical_matches
          WHERE source_division IN ('E0','SP1','D1','I1','F1')
            AND NOT (competition_code = 'FL1' AND season_label = '2019/2020')
          GROUP BY 1,2,3,4
          HAVING count(*) <> 2
             OR count(*) FILTER (WHERE home_team_norm < away_team_norm) <> 1
             OR count(*) FILTER (WHERE home_team_norm > away_team_norm) <> 1
        ) errors
        """,
    )
    appearance_errors = query_count(
        connection,
        """
        WITH team_counts AS (
          SELECT competition_code, season_label, count(DISTINCT home_team_norm) AS teams
          FROM core.historical_matches
          WHERE source_division IN ('E0','SP1','D1','I1','F1')
            AND NOT (competition_code = 'FL1' AND season_label = '2019/2020')
          GROUP BY 1,2
        ), appearances AS (
          SELECT competition_code, season_label, home_team_norm AS team,
                 count(*) AS home_games, 0::bigint AS away_games
          FROM core.historical_matches
          WHERE source_division IN ('E0','SP1','D1','I1','F1')
            AND NOT (competition_code = 'FL1' AND season_label = '2019/2020')
          GROUP BY 1,2,3
          UNION ALL
          SELECT competition_code, season_label, away_team_norm,
                 0::bigint, count(*)
          FROM core.historical_matches
          WHERE source_division IN ('E0','SP1','D1','I1','F1')
            AND NOT (competition_code = 'FL1' AND season_label = '2019/2020')
          GROUP BY 1,2,3
        )
        SELECT count(*) FROM (
          SELECT a.competition_code, a.season_label, a.team
          FROM appearances a
          JOIN team_counts tc USING (competition_code, season_label)
          GROUP BY a.competition_code, a.season_label, a.team, tc.teams
          HAVING sum(a.home_games) <> tc.teams - 1 OR sum(a.away_games) <> tc.teams - 1
        ) errors
        """,
    )
    if pair_errors or appearance_errors:
        audit.fail("double_round_robin", "At least one league-season has missing or duplicated pairings.", {
            "pairing_errors": pair_errors,
            "team_home_away_count_errors": appearance_errors,
        })
    audit.metrics["coverage"] = coverage
    audit.metrics["double_round_robin"] = {
        "pairing_errors": pair_errors,
        "team_home_away_count_errors": appearance_errors,
    }


def audit_source_files_and_rows(connection, audit: Audit) -> None:
    sources = [
        dict(row)
        for row in connection.execute(
            """
            SELECT source_file_id, source_name, source_url, source_division, season_label,
                   content_sha256, content_bytes, storage_path, parser_version,
                   row_count, status, validation_report
            FROM raw.source_files
            WHERE source_name = 'football-data.co.uk'
              AND source_division IN ('E0','SP1','D1','I1','F1')
            ORDER BY season_label, source_division
            """
        )
    ]
    expected_files = {
        (division, season)
        for season in EXPECTED_SEASONS
        for division in DIVISIONS
    }
    actual_files = {(item["source_division"], item["season_label"]) for item in sources}
    if actual_files != expected_files or len(sources) != len(expected_files):
        audit.fail("source_file_scope", "Source file set is not exactly the expected 165 files.", {
            "expected_count": len(expected_files),
            "actual_count": len(sources),
            "missing": sorted(expected_files - actual_files),
            "unexpected": sorted(actual_files - expected_files),
        })

    source_metrics: list[dict[str, Any]] = []
    reconstructed_matches = 0
    reconstructed_odds = 0
    raw_invalid_cells = 0
    invalid_quote_groups = 0

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

    for source in sources:
        context = {
            "source_file_id": int(source["source_file_id"]),
            "division": source["source_division"],
            "season": source["season_label"],
        }
        path = Path(source["storage_path"])
        if not path.is_file():
            audit.fail("source_file_missing", "Database source file path does not exist.", {
                **context,
                "path": str(path),
            })
            continue
        content = path.read_bytes()
        actual_sha = hashlib.sha256(content).hexdigest()
        if actual_sha.lower() != str(source["content_sha256"]).lower():
            audit.fail("source_hash_mismatch", "Source file SHA-256 does not match PostgreSQL.", {
                **context,
                "expected": source["content_sha256"],
                "actual": actual_sha,
                "path": str(path),
            })
        if len(content) != int(source["content_bytes"]):
            audit.fail("source_size_mismatch", "Source file byte count does not match PostgreSQL.", {
                **context,
                "expected": int(source["content_bytes"]),
                "actual": len(content),
            })
        expected_url = (
            "https://www.football-data.co.uk/mmz4281/"
            f"{SEASON_SOURCE_CODES.get(source['season_label'], '?')}/{source['source_division']}.csv"
        )
        if source["source_url"] != expected_url:
            audit.fail("source_url_mismatch", "Source URL is not the expected official archive URL.", {
                **context,
                "expected": expected_url,
                "actual": source["source_url"],
            })
        if source["status"] != "imported":
            audit.fail("source_status", "Source file is not marked imported.", {
                **context,
                "status": source["status"],
            })

        try:
            rows, encoding, headers = csv_rows(path)
        except Exception as error:
            audit.fail("source_csv_parse", "Source CSV cannot be independently parsed.", {
                **context,
                "error": str(error),
            })
            continue
        required_headers = {"Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"}
        if not required_headers.issubset(headers):
            audit.fail("source_schema", "Source CSV is missing required match columns.", {
                **context,
                "missing": sorted(required_headers - set(headers)),
            })
        if len(rows) != int(source["row_count"]):
            audit.fail("source_row_count", "CSV data-row count differs from PostgreSQL source metadata.", {
                **context,
                "csv_rows": len(rows),
                "database_rows": int(source["row_count"]),
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
            audit.fail("persisted_match_count", "Persisted match count differs from parsed source rows.", {
                **context,
                "csv_rows": len(rows),
                "database_matches": len(matches),
            })

        odds_rows: dict[tuple[int, str, str, bool], dict[str, Any]] = {}
        for odds_row in connection.execute(
            """
            SELECT hm.source_row_number, ho.bookmaker, ho.market_type, ho.is_closing,
                   ho.price_timing, ho.point_in_time_safe,
                   ho.line_value, ho.odd_home, ho.odd_draw, ho.odd_away,
                   ho.odd_over, ho.odd_under, ho.source_columns, ho.quality_flags
            FROM core.historical_odds ho
            JOIN core.historical_matches hm ON hm.hist_match_id = ho.hist_match_id
            WHERE hm.source_file_id = %s
            ORDER BY hm.source_row_number, ho.market_type, ho.bookmaker, ho.is_closing
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
            if key in odds_rows:
                audit.fail("odds_key_duplicate", "Duplicate odds key found within source file.", {
                    **context,
                    "key": list(key),
                })
            odds_rows[key] = item

        expected_odds_keys: set[tuple[int, str, str, bool]] = set()
        per_file_invalid_cells = 0
        per_file_invalid_quotes = 0
        for match in matches:
            row_number = int(match["source_row_number"])
            source_row = rows.get(row_number)
            row_context = {**context, "row": row_number}
            if source_row is None:
                audit.fail("source_row_reference", "Persisted match references a missing CSV row.", row_context)
                continue
            try:
                expected = expected_match_fields(source, row_number, source_row)
            except Exception as error:
                audit.fail("source_row_semantics", "CSV row cannot be independently normalized.", {
                    **row_context,
                    "error": str(error),
                })
                continue
            compare_values(audit, "match_source_mismatch", row_context, expected, match, match_fields)
            if source_row.get("FTR", "").upper() != expected["full_time_result"]:
                audit.fail("raw_full_time_result", "CSV FTR conflicts with its own final score.", row_context)
            raw_htr = source_row.get("HTR", "").upper() or None
            if raw_htr != expected["half_time_result"]:
                audit.fail("raw_half_time_result", "CSV HTR conflicts with its own half-time score.", row_context)
            expected_country = DIVISIONS[source["source_division"]]["country"]
            if (
                match["mapped_home_norm"] != expected["home_team_norm"]
                or match["mapped_away_norm"] != expected["away_team_norm"]
                or match["mapped_home_country"] != expected_country
                or match["mapped_away_country"] != expected_country
                or match["team_id_home"] is None
                or match["team_id_away"] is None
                or match["team_id_home"] == match["team_id_away"]
            ):
                audit.fail("match_team_mapping", "Match team IDs do not resolve to the expected normalized clubs.", {
                    **row_context,
                    "home": {
                        "source": expected["home_team_norm"],
                        "mapped": match["mapped_home_norm"],
                        "country": match["mapped_home_country"],
                    },
                    "away": {
                        "source": expected["away_team_norm"],
                        "mapped": match["mapped_away_norm"],
                        "country": match["mapped_away_country"],
                    },
                })

            quotes, invalid_quotes = expected_odds(source_row, source["season_label"])
            per_file_invalid_quotes += invalid_quotes
            cell_issues = invalid_odds_cells(source_row)
            per_file_invalid_cells += len(cell_issues)
            for quote_key, expected_quote in quotes.items():
                database_key = (row_number, *quote_key)
                expected_odds_keys.add(database_key)
                actual_quote = odds_rows.get(database_key)
                if actual_quote is None:
                    audit.fail("missing_normalized_odds", "A complete valid source quote is missing from PostgreSQL.", {
                        **row_context,
                        "odds_key": list(quote_key),
                    })
                    continue
                compare_values(
                    audit,
                    "odds_source_mismatch",
                    {**row_context, "odds_key": list(quote_key)},
                    expected_quote,
                    actual_quote,
                    odds_fields,
                )

        unexpected_odds = sorted(set(odds_rows) - expected_odds_keys)
        if unexpected_odds:
            audit.fail("unexpected_normalized_odds", "PostgreSQL contains quotes not reconstructable from valid source cells.", {
                **context,
                "count": len(unexpected_odds),
                "sample": [list(item) for item in unexpected_odds[:10]],
            })
        reconstructed_matches += len(rows)
        reconstructed_odds += len(expected_odds_keys)
        raw_invalid_cells += per_file_invalid_cells
        invalid_quote_groups += per_file_invalid_quotes

        validation = source["validation_report"] or {}
        validation_differences = {}
        expected_validation = {
            "sha256": actual_sha,
            "content_bytes": len(content),
            "valid_matches": len(rows),
            "invalid_odds_quotes": per_file_invalid_quotes,
            "invalid_odds_cell_count": per_file_invalid_cells,
            "rejected_count": 0,
        }
        for field_name, expected_value in expected_validation.items():
            actual_value = validation.get(field_name)
            if canonicalize(actual_value) != canonicalize(expected_value):
                validation_differences[field_name] = {
                    "expected": expected_value,
                    "actual": actual_value,
                }
        if validation_differences:
            audit.fail("validation_report_mismatch", "Stored validation report is not reproducible from the CSV.", {
                **context,
                "differences": validation_differences,
            })

        source_metrics.append({
            **context,
            "path": str(path),
            "sha256": actual_sha,
            "bytes": len(content),
            "encoding": encoding,
            "matches": len(rows),
            "odds": len(expected_odds_keys),
            "invalid_source_odds_cells_quarantined": per_file_invalid_cells,
            "invalid_or_incomplete_quote_groups_quarantined": per_file_invalid_quotes,
        })

    audit.metrics["source_files"] = source_metrics
    audit.metrics["reconstruction"] = {
        "source_files": len(sources),
        "matches_reconstructed": reconstructed_matches,
        "odds_reconstructed": reconstructed_odds,
        "raw_invalid_odds_cells_preserved_but_not_normalized": raw_invalid_cells,
        "invalid_or_incomplete_quote_groups_not_normalized": invalid_quote_groups,
    }
    if raw_invalid_cells:
        audit.warn(
            "raw_invalid_odds_quarantined",
            "The primary CSVs contain decimal-odds cells at or below 1; they remain in raw_row and were correctly excluded from normalized odds.",
            {"cell_count": raw_invalid_cells},
        )


def audit_result_verification(connection, audit: Audit) -> None:
    statuses = [
        dict(row)
        for row in connection.execute(
            """
            SELECT season_label, result_status, count(*) AS matches
            FROM core.historical_matches
            WHERE source_division IN ('E0','SP1','D1','I1','F1')
            GROUP BY 1,2 ORDER BY 1,2
            """
        )
    ]
    current = {
        row["result_status"]: int(row["matches"])
        for row in statuses
        if row["season_label"] == "2024/2025"
    }
    if current != {"awarded": 2, "verified_normal": 1750}:
        audit.fail("cross_source_status_counts", "2024/2025 verification statuses are not the expected 1,750 normal plus 2 awarded.", {
            "actual": current,
        })
    verification_integrity = connection.execute(
        """
        SELECT
          count(*) FILTER (WHERE result_verification_report_sha256 IS NULL) AS missing_report_hash,
          count(*) FILTER (WHERE result_verification_source IS NULL) AS missing_source,
          count(*) FILTER (WHERE NOT (quality_flags ? 'cross_source_full_time_verified')) AS missing_flag,
          count(DISTINCT result_verification_report_sha256) AS report_hashes
        FROM core.historical_matches
        WHERE season_label = '2024/2025'
          AND source_division IN ('E0','SP1','D1','I1','F1')
        """
    ).fetchone()
    if any(int(verification_integrity[key]) for key in ("missing_report_hash", "missing_source", "missing_flag")):
        audit.fail("cross_source_lineage", "One or more verified matches lack verification lineage.", {
            key: int(value) for key, value in verification_integrity.items()
        })
    if int(verification_integrity["report_hashes"]) != 1:
        audit.fail("cross_source_report_count", "2024/2025 matches do not reference exactly one pinned report hash.", {
            "distinct_report_hashes": int(verification_integrity["report_hashes"]),
        })

    expected_special = {
        ("BL1", date(2024, 12, 14), "unionberlin", "bochum"): {
            "status": "awarded",
            "ht": (None, None, None),
            "required_flag": "awarded_result_statistics_not_full_90",
            "stats_must_be_empty": True,
        },
        ("FL1", date(2025, 3, 16), "montpellier", "stetienne"): {
            "status": "awarded",
            "ht": (0, 1, "A"),
            "required_flag": "awarded_result_statistics_not_full_90",
            "stats_must_be_empty": False,
        },
        ("SA", date(2025, 2, 6), "fiorentina", "inter"): {
            "status": "verified_normal",
            "ht": (0, 0, "D"),
            "required_flag": "half_time_cross_source_conflict_official_review_prefers_primary",
            "stats_must_be_empty": False,
        },
    }
    special_rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT competition_code, match_date, home_team_norm, away_team_norm,
                   ht_home_goals, ht_away_goals, half_time_result, result_status,
                   quality_flags, match_stats
            FROM core.historical_matches
            WHERE season_label = '2024/2025'
              AND source_division IN ('E0','SP1','D1','I1','F1')
              AND (
                result_status = 'awarded'
                OR quality_flags ? 'half_time_cross_source_conflict_official_review_prefers_primary'
              )
            """
        )
    ]
    found_keys = {
        (row["competition_code"], row["match_date"], row["home_team_norm"], row["away_team_norm"])
        for row in special_rows
    }
    if found_keys != set(expected_special):
        audit.fail("special_result_set", "Awarded/conflict match set differs from reviewed evidence.", {
            "expected": [canonicalize(item) for item in sorted(expected_special)],
            "actual": [canonicalize(item) for item in sorted(found_keys)],
        })
    for row in special_rows:
        key = (row["competition_code"], row["match_date"], row["home_team_norm"], row["away_team_norm"])
        expected = expected_special.get(key)
        if expected is None:
            continue
        actual_ht = (row["ht_home_goals"], row["ht_away_goals"], row["half_time_result"])
        if (
            row["result_status"] != expected["status"]
            or actual_ht != expected["ht"]
            or expected["required_flag"] not in row["quality_flags"]
            or (expected["stats_must_be_empty"] and row["match_stats"] != {})
        ):
            audit.fail("special_result_semantics", "Reviewed awarded/conflict match was not preserved correctly.", {
                "match": canonicalize(key),
                "status": row["result_status"],
                "half_time": canonicalize(actual_ht),
                "flags": row["quality_flags"],
                "match_stats": row["match_stats"],
            })

    reports = [
        dict(row)
        for row in connection.execute(
            """
            SELECT report_sha256, report_path, matches_checked, status, summary,
                   verification_sources, warnings
            FROM audit.historical_validation_reports
            WHERE primary_source = 'football-data.co.uk' AND season_label = '2024/2025'
            """
        )
    ]
    if len(reports) != 1:
        audit.fail("validation_report_rows", "Expected exactly one applied 2024/2025 validation report.", {
            "actual_count": len(reports),
        })
    else:
        report_row = reports[0]
        report_path = Path(report_row["report_path"])
        if not report_path.is_file():
            audit.fail("validation_report_missing", "Applied cross-source report file is missing.", {
                "path": str(report_path),
            })
        else:
            content = report_path.read_bytes()
            actual_hash = hashlib.sha256(content).hexdigest()
            if actual_hash.lower() != str(report_row["report_sha256"]).lower():
                audit.fail("validation_report_hash", "Applied report SHA-256 does not match its file.", {
                    "expected": report_row["report_sha256"],
                    "actual": actual_hash,
                })
            payload = json.loads(content)
            checksum_errors: list[dict[str, Any]] = []
            for item in payload.get("checksums") or []:
                source_path = report_path.parent / item["file"]
                actual = hashlib.sha256(source_path.read_bytes()).hexdigest().upper() if source_path.is_file() else None
                expected = str(item["expected_sha256"]).upper()
                if actual != expected or not item.get("matched"):
                    checksum_errors.append({
                        "file": item["file"],
                        "expected": expected,
                        "actual": actual,
                    })
            if checksum_errors:
                audit.fail("validation_evidence_hashes", "One or more pinned cross-source evidence files changed or disappeared.", {
                    "errors": checksum_errors[:20],
                })
            summary = payload.get("summary") or {}
            if (
                int(summary.get("football_data_org_verified_full_time_scores", -1)) != 1752
                or int(summary.get("football_data_org_full_time_score_mismatches", -1)) != 0
                or int(summary.get("fatal_error_count", -1)) != 0
            ):
                audit.fail("validation_report_semantics", "Cross-source report does not prove all 1,752 full-time scores.", {
                    "summary": summary,
                })

    unverified = query_count(
        connection,
        """
        SELECT count(*) FROM core.historical_matches
        WHERE result_status = 'unverified'
          AND source_division IN ('E0','SP1','D1','I1','F1')
        """,
    )
    verified = query_count(
        connection,
        """
        SELECT count(*) FROM core.historical_matches
        WHERE result_status IN ('verified_normal','awarded','abandoned')
          AND source_division IN ('E0','SP1','D1','I1','F1')
        """,
    )
    total = unverified + verified
    audit.metrics["result_verification"] = {
        "status_counts": statuses,
        "independently_verified_matches": verified,
        "unverified_single_source_matches": unverified,
        "independent_verification_coverage_pct": round(100 * verified / total, 4) if total else 0,
    }
    if unverified:
        unverified_seasons = [
            row["season_label"]
            for row in connection.execute(
                """
                SELECT DISTINCT season_label
                FROM core.historical_matches
                WHERE result_status = 'unverified'
                  AND source_division IN ('E0','SP1','D1','I1','F1')
                ORDER BY season_label
                """
            )
        ]
        audit.warn(
            "single_source_unverified_seasons",
            "These seasons are hash-pinned and exactly reproducible from the primary source, but have not yet received independent score verification.",
            {
                "matches": unverified,
                "seasons": unverified_seasons,
            },
        )


def audit_point_in_time_risk(connection, audit: Audit) -> None:
    closing = query_count(
        connection,
        """
        SELECT count(*) FROM core.historical_odds ho
        JOIN core.historical_matches hm ON hm.hist_match_id = ho.hist_match_id
        WHERE hm.source_division IN ('E0','SP1','D1','I1','F1') AND ho.is_closing
        """,
    )
    timing_rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT price_timing, point_in_time_safe, count(*) AS quotes
            FROM core.historical_odds ho
            JOIN core.historical_matches hm ON hm.hist_match_id = ho.hist_match_id
            WHERE hm.source_division IN ('E0','SP1','D1','I1','F1')
            GROUP BY 1,2 ORDER BY 1,2
            """
        )
    ]
    pinnacle_stale = query_count(
        connection,
        """
        SELECT count(*) FROM core.historical_odds ho
        JOIN core.historical_matches hm ON hm.hist_match_id = ho.hist_match_id
        WHERE hm.season_label = '2025/2026'
          AND hm.source_division IN ('E0','SP1','D1','I1','F1')
          AND ho.bookmaker = 'Pinnacle'
          AND NOT (ho.quality_flags ? 'provider_warned_stale_after_2025-07-23')
        """,
    )
    pinnacle_total = query_count(
        connection,
        """
        SELECT count(*) FROM core.historical_odds ho
        JOIN core.historical_matches hm ON hm.hist_match_id = ho.hist_match_id
        WHERE hm.season_label = '2025/2026'
          AND hm.source_division IN ('E0','SP1','D1','I1','F1')
          AND ho.bookmaker = 'Pinnacle'
        """,
    )
    if pinnacle_stale:
        audit.fail("pinnacle_stale_flag", "2025/2026 Pinnacle quotes are missing the provider staleness warning flag.", {
            "missing_flags": pinnacle_stale,
        })
    elif pinnacle_total:
        audit.warn(
            "pinnacle_provider_staleness",
            "The provider warns that 2025/2026 Pinnacle prices may be stale after 2025-07-23. Every affected normalized quote carries the required warning flag.",
            {"flagged_quotes": pinnacle_total},
        )
    audit.metrics["odds_timing"] = timing_rows
    if closing:
        audit.warn(
            "closing_odds_not_point_in_time",
            "No historical price has a per-row capture timestamp. Opening, closing and legacy pre-closing labels are market benchmarks only and are correctly marked point_in_time_safe=false.",
            {"closing_quotes": closing, "timing_counts": timing_rows},
        )


def database_counts(connection) -> dict[str, int]:
    row = connection.execute(
        """
        SELECT
          (SELECT count(*) FROM raw.source_files
           WHERE source_name = 'football-data.co.uk'
             AND status = 'imported'
             AND source_division IN ('E0','SP1','D1','I1','F1')) AS source_files,
          (SELECT count(*) FROM core.historical_matches
           WHERE source_division IN ('E0','SP1','D1','I1','F1')) AS matches,
          (SELECT count(*) FROM core.historical_odds ho
           JOIN core.historical_matches hm ON hm.hist_match_id = ho.hist_match_id
           WHERE hm.source_division IN ('E0','SP1','D1','I1','F1')) AS odds,
          (SELECT count(DISTINCT team_id) FROM (
             SELECT team_id_home AS team_id FROM core.historical_matches
             WHERE source_division IN ('E0','SP1','D1','I1','F1')
             UNION
             SELECT team_id_away FROM core.historical_matches
             WHERE source_division IN ('E0','SP1','D1','I1','F1')
           ) scoped_teams) AS teams,
          (SELECT count(*) FROM core.team_aliases
           WHERE competition_code IN ('PL','PD','BL1','SA','FL1')) AS team_aliases,
          (SELECT count(*) FROM raw.historical_import_runs) AS import_runs
        """
    ).fetchone()
    return {key: int(value) for key, value in row.items()}


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
                   content_sha256, content_bytes, storage_path, parser_version,
                   row_count, status, validation_report
            FROM raw.source_files
            WHERE source_name = 'football-data.co.uk'
              AND source_division IN ('E0','SP1','D1','I1','F1')
            ORDER BY source_division, season_label, source_url, content_sha256
            """,
            "audit_hash_sources",
        ),
        "matches": hash_query(
            connection,
            """
            SELECT hm.competition_code, hm.source_division, hm.season_label,
                   hm.match_date, hm.match_time_local, hm.source_timezone,
                   hm.home_team, hm.away_team, hm.home_team_norm, hm.away_team_norm,
                   hm.home_goals, hm.away_goals, hm.full_time_result,
                   hm.result_status, hm.result_verification_source,
                   hm.result_verification_report_sha256,
                   hm.ht_home_goals, hm.ht_away_goals, hm.half_time_result,
                   ht.normalized_name AS mapped_home_norm, ht.country_code AS mapped_home_country,
                   at.normalized_name AS mapped_away_norm, at.country_code AS mapped_away_country,
                   sf.content_sha256 AS source_file_sha256,
                   hm.source_row_number, hm.source_row_hash,
                   hm.match_stats, hm.quality_flags, hm.raw_row
            FROM core.historical_matches hm
            JOIN raw.source_files sf ON sf.source_file_id = hm.source_file_id
            LEFT JOIN core.teams ht ON ht.team_id = hm.team_id_home
            LEFT JOIN core.teams at ON at.team_id = hm.team_id_away
            WHERE hm.source_division IN ('E0','SP1','D1','I1','F1')
            ORDER BY hm.source_division, hm.season_label, hm.match_date,
                     hm.home_team_norm, hm.away_team_norm
            """,
            "audit_hash_matches",
        ),
        "odds": hash_query(
            connection,
            """
            SELECT hm.source_division, hm.season_label, hm.match_date,
                   hm.home_team_norm, hm.away_team_norm,
                   ho.bookmaker, ho.market_type, ho.is_closing,
                   ho.price_timing, ho.point_in_time_safe,
                   ho.line_value, ho.odd_home, ho.odd_draw, ho.odd_away,
                   ho.odd_over, ho.odd_under, ho.source_columns, ho.quality_flags
            FROM core.historical_odds ho
            JOIN core.historical_matches hm ON hm.hist_match_id = ho.hist_match_id
            WHERE hm.source_division IN ('E0','SP1','D1','I1','F1')
            ORDER BY hm.source_division, hm.season_label, hm.match_date,
                     hm.home_team_norm, hm.away_team_norm,
                     ho.market_type, ho.bookmaker, ho.is_closing
            """,
            "audit_hash_odds",
        ),
        "team_aliases": hash_query(
            connection,
            """
            SELECT a.source_name, a.competition_code, a.source_alias,
                   a.normalized_alias, a.verified, a.verification_method,
                   a.evidence, t.canonical_name, t.normalized_name, t.country_code
            FROM core.team_aliases a
            JOIN core.teams t ON t.team_id = a.team_id
            WHERE a.competition_code IN ('PL','PD','BL1','SA','FL1')
            ORDER BY a.source_name, a.competition_code, a.normalized_alias
            """,
            "audit_hash_aliases",
        ),
    }


def compare_baseline(
    audit: Audit,
    baseline_path: Path | None,
    counts: dict[str, int],
    fingerprints: dict[str, str],
) -> dict[str, Any]:
    if baseline_path is None:
        return {"status": "not_run", "message": "No baseline report supplied."}
    path = baseline_path.resolve()
    baseline = json.loads(path.read_text(encoding="utf-8"))
    old_counts = baseline.get("database_counts") or {}
    old_fingerprints = baseline.get("logical_fingerprints") or {}
    # The supplied pre-Nordic baseline was generated when team/team-alias
    # metrics were global.  After scoping this auditor to the five divisions,
    # only compare fields whose definitions are identical in both reports.
    # Team identity and alias integrity are still checked independently during
    # the source reconstruction above.
    stable_count_fields = ("source_files", "matches", "odds")
    count_changes = {
        key: {"before": old_counts.get(key), "after": counts.get(key)}
        for key in stable_count_fields
        if old_counts.get(key) != counts.get(key)
    }
    comparable_fingerprint_fields = ("source_files", "matches", "odds")
    fingerprint_changes = {
        key: {"before": old_fingerprints.get(key), "after": fingerprints.get(key)}
        for key in comparable_fingerprint_fields
        if old_fingerprints.get(key) != fingerprints.get(key)
    }
    result = {
        "status": "pass" if not count_changes and not fingerprint_changes else "fail",
        "baseline_report": str(path),
        "stable_count_fields": list(stable_count_fields),
        "comparable_fingerprint_fields": list(comparable_fingerprint_fields),
        "non_comparable_baseline_fields": ["teams", "team_aliases"],
        "count_changes": count_changes,
        "fingerprint_changes": fingerprint_changes,
        "operational_import_runs_before": old_counts.get("import_runs"),
        "operational_import_runs_after": counts.get("import_runs"),
    }
    if result["status"] == "fail":
        audit.fail("idempotency", "Repeated import changed logical rows or stable row counts.", result)
    return result


def audit_repeated_import_runs(connection, audit: Audit) -> dict[str, Any]:
    rows = [
        dict(row)
        for row in connection.execute(
            """
            SELECT import_run_id, requested_files, imported_files,
                   matches_upserted, odds_upserted, status, error_summary, report
            FROM raw.historical_import_runs
            WHERE requested_files = 165
            ORDER BY import_run_id DESC
            LIMIT 2
            """
        )
    ]
    summaries: list[dict[str, Any]] = []
    input_fingerprints: list[str] = []
    for row in rows:
        report = row.get("report") or {}
        files = report.get("files") or []
        pinned_inputs = sorted(
            (
                item.get("url"),
                item.get("sha256"),
                (item.get("validation") or {}).get("valid_matches"),
                (item.get("database") or {}).get("matches_upserted"),
                (item.get("database") or {}).get("odds_upserted"),
            )
            for item in files
        )
        input_fingerprint = hashlib.sha256(
            canonical_json(pinned_inputs).encode("utf-8")
        ).hexdigest()
        input_fingerprints.append(input_fingerprint)
        summaries.append({
            "import_run_id": int(row["import_run_id"]),
            "requested_files": int(row["requested_files"]),
            "imported_files": int(row["imported_files"]),
            "matches_processed": int(row["matches_upserted"]),
            "odds_processed": int(row["odds_upserted"]),
            "status": row["status"],
            "error_summary": row["error_summary"],
            "reported_file_count": len(files),
            "pinned_input_fingerprint": input_fingerprint,
        })
    passed = (
        len(summaries) == 2
        and all(item["status"] == "succeeded" for item in summaries)
        and all(item["requested_files"] == 165 for item in summaries)
        and all(item["imported_files"] == 165 for item in summaries)
        and all(item["matches_processed"] == 59079 for item in summaries)
        and summaries[0]["odds_processed"] == summaries[1]["odds_processed"]
        and all(item["error_summary"] is None for item in summaries)
        and all(item["reported_file_count"] == 165 for item in summaries)
        and len(set(input_fingerprints)) == 1
    )
    result = {
        "status": "pass" if passed else "fail",
        "method": "two_consecutive_full_offline_import_runs_with_identical_hash_pinned_inputs",
        "runs": summaries,
    }
    if not passed:
        audit.fail(
            "idempotency_run_evidence",
            "The two latest full imports do not provide consistent idempotency evidence.",
            result,
        )
    return result


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = parse_args(argv)
    audit = Audit()
    started = datetime.now(UTC)

    with connect() as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        counts = database_counts(connection)
        audit_global_constraints(connection, audit)
        audit_schedule_coverage(connection, audit)
        audit_source_files_and_rows(connection, audit)
        audit_result_verification(connection, audit)
        audit_point_in_time_risk(connection, audit)
        fingerprints = logical_fingerprints(connection)
        idempotency = (
            compare_baseline(audit, args.baseline_report, counts, fingerprints)
            if args.baseline_report
            else audit_repeated_import_runs(connection, audit)
        )

    reconstructed = audit.metrics.get("reconstruction") or {}
    if reconstructed.get("matches_reconstructed") != counts["matches"]:
        audit.fail("reconstruction_total_matches", "Total reconstructed matches differ from PostgreSQL.", {
            "reconstructed": reconstructed.get("matches_reconstructed"),
            "database": counts["matches"],
        })
    if reconstructed.get("odds_reconstructed") != counts["odds"]:
        audit.fail("reconstruction_total_odds", "Total reconstructed odds differ from PostgreSQL.", {
            "reconstructed": reconstructed.get("odds_reconstructed"),
            "database": counts["odds"],
        })

    fatal_codes = {item.code for item in audit.findings if item.severity == "fatal"}
    warning_codes = {item.code for item in audit.findings if item.severity == "warning"}
    fatal_total = sum(count for code, count in audit._sample_counts.items() if code in fatal_codes)
    warning_total = sum(count for code, count in audit._sample_counts.items() if code in warning_codes)
    status = "fail" if fatal_total else "pass_with_warnings" if warning_total else "pass"
    report = {
        "audit_version": AUDIT_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "duration_seconds": round((datetime.now(UTC) - started).total_seconds(), 3),
        "status": status,
        "scope": {
            "source": SOURCE_NAME,
            "leagues": list(DIVISIONS),
            "seasons": list(EXPECTED_SEASONS),
        },
        "database_counts": counts,
        "summary": {
            "fatal_findings": fatal_total,
            "warning_findings": warning_total,
            "all_source_files_hash_matched": not any(
                item.code in {"source_file_missing", "source_hash_mismatch", "source_size_mismatch"}
                for item in audit.findings
            ),
            "all_matches_reconstructed_from_source": reconstructed.get("matches_reconstructed") == counts["matches"],
            "all_odds_reconstructed_from_source": reconstructed.get("odds_reconstructed") == counts["odds"],
            "normalized_illegal_odds": audit.metrics.get("global_constraints", {}).get("invalid_normalized_odds"),
            "independent_verification_coverage_pct": audit.metrics.get("result_verification", {}).get(
                "independent_verification_coverage_pct"
            ),
        },
        "metrics": audit.metrics,
        "logical_fingerprints": fingerprints,
        "idempotency": idempotency,
        "finding_occurrences": dict(sorted(audit._sample_counts.items())),
        "findings": [asdict(item) for item in audit.findings],
    }
    rendered = json.dumps(canonicalize(report), ensure_ascii=False, indent=2)
    if args.output:
        output_path = args.output.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    print(json.dumps({
        "status": status,
        "database_counts": counts,
        "summary": report["summary"],
        "reconstruction": reconstructed,
        "idempotency": idempotency,
        "output": str(args.output.resolve()) if args.output else None,
    }, ensure_ascii=False, indent=2))
    return 2 if status == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
