#!/usr/bin/env python3
"""Audit immutable football-data.co.uk CSV files without changing them.

The tool deliberately separates:
1. provenance/fixity (official URL + SHA-256 manifest),
2. internal semantic consistency,
3. independent cross-source verification.

It never fills, repairs, or invents missing values.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


AUDIT_VERSION = "1.0.0"
DEFAULT_LEAGUES = ("E0", "D1", "I1", "SP1", "F1")
OFFICIAL_HOSTS = {"football-data.co.uk", "www.football-data.co.uk"}
REQUIRED_COLUMNS = ("Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR")
PLACEHOLDER_TEAMS = {
    "away team",
    "demo",
    "example",
    "home team",
    "mock",
    "team a",
    "team b",
    "test",
    "tbd",
    "unknown",
}
OPENING_1X2_PREFIXES = (
    "B365",
    "BW",
    "BF",
    "IW",
    "PS",
    "WH",
    "VC",
    "1XB",
    "Max",
    "Avg",
    "BFE",
)
CLOSING_1X2_PREFIXES = (
    "B365C",
    "BWC",
    "BFC",
    "IWC",
    "PSC",
    "WHC",
    "VCC",
    "1XBC",
    "MaxC",
    "AvgC",
    "BFEC",
)
ODDS_CAVEAT_DATE = date(2025, 7, 23)


@dataclass
class Finding:
    severity: str
    code: str
    message: str
    file: str | None = None
    row: int | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class FileAudit:
    path: str
    sha256: str
    byte_size: int
    encoding: str | None = None
    league: str | None = None
    season: str | None = None
    rows: int = 0
    completed_rows: int = 0
    incomplete_rows: int = 0
    teams: int = 0
    date_min: str | None = None
    date_max: str | None = None
    opening_1x2_rows: int = 0
    closing_1x2_rows: int = 0
    odds_group_coverage: dict[str, int] = field(default_factory=dict)
    deterministic_sample: list[dict[str, Any]] = field(default_factory=list)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize_key(value: str) -> str:
    return " ".join(value.casefold().split())


def loose_team_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    ascii_only = "".join(char for char in normalized if not unicodedata.combining(char))
    tokens = re.findall(r"[a-z0-9]+", ascii_only)
    ignored = {"afc", "cf", "fc", "football", "club"}
    return " ".join(token for token in tokens if token not in ignored)


def parse_season(value: str | None) -> tuple[int, int, str] | None:
    if not value:
        return None
    digits = re.findall(r"\d+", value)
    if len(digits) == 1 and len(digits[0]) == 4:
        token = digits[0]
        start = 2000 + int(token[:2])
        end = 2000 + int(token[2:])
    elif len(digits) >= 2:
        start = int(digits[0])
        end = int(digits[1])
        if start < 100:
            start += 2000
        if end < 100:
            end = (start // 100) * 100 + end
            if end < start:
                end += 100
    else:
        raise ValueError(f"Cannot parse season: {value!r}")
    if end != start + 1:
        raise ValueError(f"Season must span consecutive years: {value!r}")
    return start, end, f"{start}/{end}"


def parse_match_date(value: str) -> date:
    cleaned = value.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date: {value!r}")


def parse_int(value: str, field_name: str) -> int:
    cleaned = value.strip()
    if not re.fullmatch(r"-?\d+", cleaned):
        raise ValueError(f"{field_name} is not an integer: {value!r}")
    return int(cleaned)


def parse_odd(value: str, field_name: str) -> float:
    try:
        number = float(value.strip())
    except ValueError as error:
        raise ValueError(f"{field_name} is not numeric: {value!r}") from error
    if not (1.0 < number <= 1000.0):
        raise ValueError(f"{field_name} must be in (1, 1000], got {number}")
    return number


def read_csv_rows(path: Path) -> tuple[str, list[str], list[dict[str, str]], int]:
    raw = path.read_bytes()
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
            reader = csv.DictReader(text.splitlines())
            if not reader.fieldnames:
                raise ValueError("CSV has no header")
            fieldnames = [str(name).strip() for name in reader.fieldnames]
            rows: list[dict[str, str]] = []
            blank_rows = 0
            for source_row in reader:
                cleaned = {
                    str(key).strip(): ("" if value is None else str(value).strip())
                    for key, value in source_row.items()
                    if key is not None
                }
                if any(cleaned.values()):
                    rows.append(cleaned)
                else:
                    blank_rows += 1
            return encoding, fieldnames, rows, blank_rows
        except (UnicodeDecodeError, csv.Error, ValueError) as error:
            last_error = error
    raise ValueError(f"Unable to parse CSV: {last_error}")


def discover_manifest(root: Path, explicit: str | None) -> Path | None:
    if explicit:
        return Path(explicit).resolve()
    candidates = []
    for pattern in ("manifest.json", "source_manifest.json", "download_manifest.json"):
        candidates.extend(root.rglob(pattern))
    return sorted(candidates)[0] if candidates else None


def load_manifest(path: Path | None) -> tuple[dict[str, dict[str, Any]], str | None]:
    if path is None:
        return {}, None
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict):
        entries = next(
            (
                payload[key]
                for key in ("files", "artifacts", "downloads", "sources")
                if isinstance(payload.get(key), list)
            ),
            [],
        )
    else:
        entries = []
    by_name: dict[str, dict[str, Any]] = {}
    global_download_time = (
        payload.get("retrieved_at_utc") if isinstance(payload, dict) else None
    )
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        candidate = (
            entry.get("path")
            or entry.get("file")
            or entry.get("filename")
            or entry.get("local_path")
        )
        if candidate:
            normalized_entry = dict(entry)
            if global_download_time:
                normalized_entry.setdefault("downloaded_at_utc", global_download_time)
            by_name[Path(str(candidate)).name.casefold()] = normalized_entry
    return by_name, sha256_file(path)


def manifest_value(entry: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = entry.get(key)
        if value not in (None, ""):
            return value
    return None


def finding(
    findings: list[Finding],
    severity: str,
    code: str,
    message: str,
    path: Path | None = None,
    row: int | None = None,
    **evidence: Any,
) -> None:
    findings.append(
        Finding(
            severity=severity,
            code=code,
            message=message,
            file=str(path.resolve()) if path else None,
            row=row,
            evidence=evidence,
        )
    )


def validate_manifest_entry(
    path: Path,
    entry: dict[str, Any] | None,
    digest: str,
    findings: list[Finding],
) -> None:
    if entry is None:
        finding(
            findings,
            "fail",
            "provenance.manifest_entry_missing",
            "File has no manifest entry; authenticity and immutability are not established.",
            path,
        )
        return

    url = str(manifest_value(entry, "url", "source_url", "download_url", "final_url") or "")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in OFFICIAL_HOSTS:
        finding(
            findings,
            "fail",
            "provenance.untrusted_url",
            "Source URL is not an HTTPS football-data.co.uk URL.",
            path,
            url=url,
        )

    expected_hash = str(manifest_value(entry, "sha256", "sha_256", "digest") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        finding(
            findings,
            "fail",
            "provenance.sha256_missing",
            "Manifest must contain a 64-character SHA-256 digest.",
            path,
        )
    elif expected_hash != digest:
        finding(
            findings,
            "fail",
            "provenance.sha256_mismatch",
            "File content changed after the manifest was created.",
            path,
            expected=expected_hash,
            actual=digest,
        )

    expected_size = manifest_value(entry, "bytes", "byte_size", "size")
    if expected_size is not None:
        try:
            if int(expected_size) != path.stat().st_size:
                finding(
                    findings,
                    "fail",
                    "provenance.byte_size_mismatch",
                    "File size differs from the manifest.",
                    path,
                    expected=int(expected_size),
                    actual=path.stat().st_size,
                )
        except (TypeError, ValueError):
            finding(
                findings,
                "warn",
                "provenance.byte_size_invalid",
                "Manifest byte-size field is not an integer.",
                path,
            )

    status = manifest_value(entry, "http_status", "status_code", "status")
    if status is not None:
        try:
            if int(status) != 200:
                finding(
                    findings,
                    "fail",
                    "provenance.http_status",
                    "Download manifest does not record HTTP 200.",
                    path,
                    status=status,
                )
        except (TypeError, ValueError):
            finding(
                findings,
                "warn",
                "provenance.http_status_invalid",
                "Manifest HTTP status is not numeric.",
                path,
                status=status,
            )

    downloaded_at = manifest_value(
        entry, "downloaded_at_utc", "fetched_at_utc", "downloaded_at", "fetched_at"
    )
    if not downloaded_at:
        finding(
            findings,
            "warn",
            "provenance.download_time_missing",
            "Manifest has no UTC download timestamp.",
            path,
        )

    if entry.get("synthetic") is True or entry.get("generated") is True:
        finding(
            findings,
            "fail",
            "authenticity.synthetic_declared",
            "Manifest declares generated/synthetic content; production ingestion is forbidden.",
            path,
        )


def validate_odds_triplet(
    row: dict[str, str],
    columns: tuple[str, str, str],
    path: Path,
    row_number: int,
    findings: list[Finding],
) -> bool:
    values = [row.get(column, "").strip() for column in columns]
    present = [bool(value) for value in values]
    if not any(present):
        return False
    if not all(present):
        finding(
            findings,
            "warn",
            "odds.partial_triplet",
            "A 1X2 odds triplet is only partially populated.",
            path,
            row_number,
            columns=columns,
            values=values,
        )
        return False
    odds = []
    invalid: dict[str, str] = {}
    for value, column in zip(values, columns):
        try:
            odds.append(parse_odd(value, column))
        except ValueError:
            invalid[column] = value
    if invalid:
        finding(
            findings,
            "fail",
            "odds.invalid",
            "Odds cells must contain decimal values greater than 1; quarantine invalid cells as null.",
            path,
            row_number,
            invalid=invalid,
        )
        return False
    overround = sum(1.0 / odd for odd in odds)
    is_cross_book_maximum = columns[0].startswith("Max")
    if not 0.75 <= overround <= 2.0:
        finding(
            findings,
            "fail",
            "odds.impossible_overround",
            "1X2 implied-probability sum is outside a broad legal range.",
            path,
            row_number,
            columns=columns,
            overround=round(overround, 6),
        )
    elif not is_cross_book_maximum and not 0.9 <= overround <= 1.5:
        finding(
            findings,
            "warn",
            "odds.unusual_overround",
            "1X2 implied-probability sum is unusual and needs review.",
            path,
            row_number,
            columns=columns,
            overround=round(overround, 6),
        )
    return True


def validate_odds_pair(
    row: dict[str, str],
    columns: tuple[str, str],
    path: Path,
    row_number: int,
    findings: list[Finding],
) -> bool:
    values = [row.get(column, "").strip() for column in columns]
    present = [bool(value) for value in values]
    if not any(present):
        return False
    if not all(present):
        finding(
            findings,
            "warn",
            "odds.partial_pair",
            "A two-sided odds market is only partially populated.",
            path,
            row_number,
            columns=columns,
            values=values,
        )
        return False
    invalid = {}
    for value, column in zip(values, columns):
        try:
            parse_odd(value, column)
        except ValueError:
            invalid[column] = value
    if invalid:
        finding(
            findings,
            "fail",
            "odds.invalid",
            "Odds cells must contain decimal values greater than 1; quarantine invalid cells as null.",
            path,
            row_number,
            invalid=invalid,
        )
        return False
    return True


def validate_max_average(
    row: dict[str, str],
    max_columns: tuple[str, str, str],
    avg_columns: tuple[str, str, str],
    path: Path,
    row_number: int,
    findings: list[Finding],
) -> None:
    for maximum_column, average_column in zip(max_columns, avg_columns):
        maximum = row.get(maximum_column, "").strip()
        average = row.get(average_column, "").strip()
        if not maximum or not average:
            continue
        try:
            maximum_value = parse_odd(maximum, maximum_column)
            average_value = parse_odd(average, average_column)
        except ValueError:
            continue
        if maximum_value + 1e-9 < average_value:
            finding(
                findings,
                "fail",
                "odds.max_below_average",
                "Market maximum odds are below market average odds.",
                path,
                row_number,
                max_column=maximum_column,
                max_value=maximum_value,
                avg_column=average_column,
                avg_value=average_value,
            )


def deterministic_sample(
    rows: Iterable[dict[str, Any]], sample_size: int
) -> list[dict[str, Any]]:
    ranked = []
    for row in rows:
        key = "|".join(
            str(row.get(field, ""))
            for field in ("Div", "_parsed_date", "HomeTeam", "AwayTeam", "FTHG", "FTAG")
        )
        ranked.append((hashlib.sha256(key.encode("utf-8")).hexdigest(), row))
    output = []
    for _, row in sorted(ranked)[:sample_size]:
        output.append(
            {
                "league": row.get("Div"),
                "date": row.get("_parsed_date"),
                "home_team": row.get("HomeTeam"),
                "away_team": row.get("AwayTeam"),
                "home_goals": row.get("FTHG"),
                "away_goals": row.get("FTAG"),
            }
        )
    return output


def expected_team_count(league: str, season_start: int | None) -> int | None:
    if league == "E0":
        return 22 if season_start is not None and season_start <= 1994 else 20
    if league == "I1":
        return 18 if season_start is not None and season_start <= 2003 else 20
    if league == "SP1":
        return 22 if season_start in {1995, 1996} else 20
    if league == "D1":
        return 18
    if league == "F1" and season_start is not None:
        return 18 if season_start >= 2023 else 20
    return None


def audit_file(
    path: Path,
    manifest_entry: dict[str, Any] | None,
    season_info: tuple[int, int, str] | None,
    require_complete: bool,
    sample_size: int,
    findings: list[Finding],
) -> tuple[FileAudit, list[dict[str, Any]]]:
    digest = sha256_file(path)
    result = FileAudit(
        path=str(path.resolve()),
        sha256=digest,
        byte_size=path.stat().st_size,
        season=season_info[2] if season_info else None,
    )
    validate_manifest_entry(path, manifest_entry, digest, findings)

    try:
        encoding, fieldnames, rows, blank_rows = read_csv_rows(path)
    except ValueError as error:
        finding(findings, "fail", "csv.parse_failed", str(error), path)
        return result, []

    result.encoding = encoding
    result.rows = len(rows)
    unnamed_columns = sum(not name for name in fieldnames)
    duplicate_named_columns = sorted(
        name
        for name, count in Counter(name for name in fieldnames if name).items()
        if count > 1
    )
    if unnamed_columns:
        finding(
            findings,
            "warn",
            "schema.unnamed_padding_columns",
            "CSV header contains unnamed padding columns; importer must ignore them.",
            path,
            header_cells=len(fieldnames),
            unnamed_columns=unnamed_columns,
            effective_named_columns=len(fieldnames) - unnamed_columns,
        )
    if duplicate_named_columns:
        finding(
            findings,
            "fail",
            "schema.duplicate_named_columns",
            "CSV header contains duplicate non-empty field names.",
            path,
            columns=duplicate_named_columns,
        )
    if blank_rows:
        finding(
            findings,
            "warn",
            "csv.blank_padding_rows_ignored",
            "Purely empty physical rows were ignored; importer must not create fixtures from them.",
            path,
            blank_rows=blank_rows,
        )
        if manifest_entry and manifest_entry.get("padding_blank_rows") is not None:
            if int(manifest_entry["padding_blank_rows"]) != blank_rows:
                finding(
                    findings,
                    "fail",
                    "provenance.blank_row_count_mismatch",
                    "Observed blank padding row count differs from the manifest.",
                    path,
                    expected=int(manifest_entry["padding_blank_rows"]),
                    actual=blank_rows,
                )
    if manifest_entry:
        expected_records = manifest_value(manifest_entry, "records", "record_count", "rows")
        if expected_records is not None and int(expected_records) != len(rows):
            finding(
                findings,
                "fail",
                "provenance.record_count_mismatch",
                "Parsed CSV row count differs from the manifest.",
                path,
                expected=int(expected_records),
                actual=len(rows),
            )
        expected_columns = manifest_value(manifest_entry, "columns", "column_count")
        if expected_columns is not None and int(expected_columns) != len(fieldnames):
            finding(
                findings,
                "fail",
                "provenance.column_count_mismatch",
                "Parsed CSV column count differs from the manifest.",
                path,
                expected=int(expected_columns),
                actual=len(fieldnames),
            )
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
    if missing_columns:
        finding(
            findings,
            "fail",
            "schema.required_columns_missing",
            "Required match columns are missing.",
            path,
            missing=missing_columns,
        )
        return result, []

    league_values = Counter(row.get("Div", "").strip() for row in rows)
    nonempty_leagues = [value for value in league_values if value]
    if len(nonempty_leagues) != 1:
        finding(
            findings,
            "fail",
            "league.mixed_or_missing",
            "Each raw file must contain exactly one non-empty competition code.",
            path,
            values=dict(league_values),
        )
    else:
        result.league = nonempty_leagues[0]
        if path.stem.upper() != result.league.upper():
            finding(
                findings,
                "warn",
                "league.filename_mismatch",
                "CSV filename does not match its Div code.",
                path,
                filename=path.name,
                div=result.league,
            )

    parsed_rows: list[dict[str, Any]] = []
    dates: list[date] = []
    teams: set[str] = set()
    exact_keys: Counter[tuple[str, str, str, str]] = Counter()
    appearances: Counter[str] = Counter()
    home_appearances: Counter[str] = Counter()
    away_appearances: Counter[str] = Counter()
    loose_names: dict[str, set[str]] = defaultdict(set)

    opening_groups = [
        (f"{prefix}H", f"{prefix}D", f"{prefix}A")
        for prefix in OPENING_1X2_PREFIXES
        if all(f"{prefix}{suffix}" in fieldnames for suffix in ("H", "D", "A"))
    ]
    closing_groups = [
        (f"{prefix}H", f"{prefix}D", f"{prefix}A")
        for prefix in CLOSING_1X2_PREFIXES
        if all(f"{prefix}{suffix}" in fieldnames for suffix in ("H", "D", "A"))
    ]
    opening_pairs = [
        pair
        for pair in (
            ("B365>2.5", "B365<2.5"),
            ("P>2.5", "P<2.5"),
            ("Max>2.5", "Max<2.5"),
            ("Avg>2.5", "Avg<2.5"),
            ("BFE>2.5", "BFE<2.5"),
            ("B365AHH", "B365AHA"),
            ("PAHH", "PAHA"),
            ("MaxAHH", "MaxAHA"),
            ("AvgAHH", "AvgAHA"),
            ("BFEAHH", "BFEAHA"),
        )
        if all(column in fieldnames for column in pair)
    ]
    closing_pairs = [
        pair
        for pair in (
            ("B365C>2.5", "B365C<2.5"),
            ("PC>2.5", "PC<2.5"),
            ("MaxC>2.5", "MaxC<2.5"),
            ("AvgC>2.5", "AvgC<2.5"),
            ("BFEC>2.5", "BFEC<2.5"),
            ("B365CAHH", "B365CAHA"),
            ("PCAHH", "PCAHA"),
            ("MaxCAHH", "MaxCAHA"),
            ("AvgCAHH", "AvgCAHA"),
            ("BFECAHH", "BFECAHA"),
        )
        if all(column in fieldnames for column in pair)
    ]
    group_coverage: Counter[str] = Counter()

    for index, row in enumerate(rows, start=2):
        home = row.get("HomeTeam", "").strip()
        away = row.get("AwayTeam", "").strip()
        if not home or not away:
            finding(
                findings,
                "fail",
                "team.missing",
                "HomeTeam and AwayTeam must be non-empty.",
                path,
                index,
            )
            continue
        if normalize_key(home) == normalize_key(away):
            finding(
                findings,
                "fail",
                "team.self_match",
                "Home and away teams are identical.",
                path,
                index,
                team=home,
            )
        if normalize_key(home) in PLACEHOLDER_TEAMS or normalize_key(away) in PLACEHOLDER_TEAMS:
            finding(
                findings,
                "fail",
                "authenticity.placeholder_team",
                "Placeholder/demo team name detected.",
                path,
                index,
                home=home,
                away=away,
            )

        try:
            match_date = parse_match_date(row.get("Date", ""))
        except ValueError as error:
            finding(findings, "fail", "date.invalid", str(error), path, index)
            continue
        dates.append(match_date)
        if match_date > datetime.now(timezone.utc).date():
            finding(
                findings,
                "fail" if require_complete else "warn",
                "date.future_record",
                "Historical data contains a future fixture.",
                path,
                index,
                date=match_date.isoformat(),
            )
        if season_info:
            start_year, end_year, _ = season_info
            earliest = date(start_year, 7, 1)
            latest = date(end_year, 6, 30)
            if not earliest <= match_date <= latest:
                finding(
                    findings,
                    "fail",
                    "date.outside_season",
                    "Match date falls outside the declared European season.",
                    path,
                    index,
                    date=match_date.isoformat(),
                    earliest=earliest.isoformat(),
                    latest=latest.isoformat(),
                )

        div = row.get("Div", "").strip()
        key = (div, match_date.isoformat(), normalize_key(home), normalize_key(away))
        exact_keys[key] += 1
        teams.update((home, away))
        appearances.update((home, away))
        home_appearances[home] += 1
        away_appearances[away] += 1
        loose_names[loose_team_key(home)].add(home)
        loose_names[loose_team_key(away)].add(away)

        score_values = (row.get("FTHG", "").strip(), row.get("FTAG", "").strip())
        result_value = row.get("FTR", "").strip().upper()
        complete_result = bool(score_values[0] and score_values[1] and result_value)
        if not complete_result:
            result.incomplete_rows += 1
            finding(
                findings,
                "fail" if require_complete else "warn",
                "result.incomplete",
                "Completed-history row lacks full-time score or result.",
                path,
                index,
                values={"FTHG": score_values[0], "FTAG": score_values[1], "FTR": result_value},
            )
        else:
            try:
                home_goals = parse_int(score_values[0], "FTHG")
                away_goals = parse_int(score_values[1], "FTAG")
                if not 0 <= home_goals <= 50 or not 0 <= away_goals <= 50:
                    raise ValueError("Full-time goals must be between 0 and 50")
                expected_result = "H" if home_goals > away_goals else "A" if away_goals > home_goals else "D"
                if result_value != expected_result:
                    finding(
                        findings,
                        "fail",
                        "result.score_mismatch",
                        "FTR contradicts the full-time score.",
                        path,
                        index,
                        score=f"{home_goals}-{away_goals}",
                        ftr=result_value,
                        expected=expected_result,
                    )
                result.completed_rows += 1
            except ValueError as error:
                finding(findings, "fail", "result.invalid_score", str(error), path, index)

        if all(column in fieldnames for column in ("HTHG", "HTAG")):
            half_home = row.get("HTHG", "").strip()
            half_away = row.get("HTAG", "").strip()
            half_result = row.get("HTR", "").strip()
            if complete_result and not (half_home and half_away and half_result):
                finding(
                    findings,
                    "warn",
                    "result.halftime_missing",
                    "Completed result has missing half-time data; preserve nulls and do not impute zero.",
                    path,
                    index,
                    match={
                        "date": match_date.isoformat(),
                        "home": home,
                        "away": away,
                        "score": f"{score_values[0]}-{score_values[1]}",
                    },
                    missing=[
                        field
                        for field, value in (
                            ("HTHG", half_home),
                            ("HTAG", half_away),
                            ("HTR", half_result),
                        )
                        if not value
                    ],
                )
            if half_home and half_away and score_values[0] and score_values[1]:
                try:
                    hthg = parse_int(half_home, "HTHG")
                    htag = parse_int(half_away, "HTAG")
                    fthg = parse_int(score_values[0], "FTHG")
                    ftag = parse_int(score_values[1], "FTAG")
                    if min(hthg, htag) < 0 or hthg > fthg or htag > ftag:
                        finding(
                            findings,
                            "fail",
                            "result.halftime_impossible",
                            "Half-time score cannot exceed full-time score.",
                            path,
                            index,
                            halftime=f"{hthg}-{htag}",
                            fulltime=f"{fthg}-{ftag}",
                        )
                    htr = row.get("HTR", "").strip().upper()
                    if htr:
                        expected_htr = "H" if hthg > htag else "A" if htag > hthg else "D"
                        if htr != expected_htr:
                            finding(
                                findings,
                                "fail",
                                "result.halftime_result_mismatch",
                                "HTR contradicts the half-time score.",
                                path,
                                index,
                                htr=htr,
                                expected=expected_htr,
                            )
                except ValueError as error:
                    finding(findings, "fail", "result.invalid_halftime", str(error), path, index)

        opening_results = []
        for group in opening_groups:
            is_complete = validate_odds_triplet(row, group, path, index, findings)
            opening_results.append(is_complete)
            group_coverage["/".join(group)] += int(is_complete)
        closing_results = []
        for group in closing_groups:
            is_complete = validate_odds_triplet(row, group, path, index, findings)
            closing_results.append(is_complete)
            group_coverage["/".join(group)] += int(is_complete)
        for pair in opening_pairs:
            is_complete = validate_odds_pair(row, pair, path, index, findings)
            group_coverage["/".join(pair)] += int(is_complete)
        for pair in closing_pairs:
            is_complete = validate_odds_pair(row, pair, path, index, findings)
            group_coverage["/".join(pair)] += int(is_complete)
        opening_present = any(opening_results)
        closing_present = any(closing_results)
        result.opening_1x2_rows += int(opening_present)
        result.closing_1x2_rows += int(closing_present)

        if all(column in fieldnames for column in ("MaxH", "MaxD", "MaxA", "AvgH", "AvgD", "AvgA")):
            validate_max_average(
                row,
                ("MaxH", "MaxD", "MaxA"),
                ("AvgH", "AvgD", "AvgA"),
                path,
                index,
                findings,
            )
        if all(
            column in fieldnames
            for column in ("MaxCH", "MaxCD", "MaxCA", "AvgCH", "AvgCD", "AvgCA")
        ):
            validate_max_average(
                row,
                ("MaxCH", "MaxCD", "MaxCA"),
                ("AvgCH", "AvgCD", "AvgCA"),
                path,
                index,
                findings,
            )

        enriched = dict(row)
        enriched["_parsed_date"] = match_date.isoformat()
        parsed_rows.append(enriched)

    duplicates = {key: count for key, count in exact_keys.items() if count > 1}
    if duplicates:
        finding(
            findings,
            "fail",
            "match.duplicates",
            "Duplicate league/date/home/away match keys found.",
            path,
            duplicates={"|".join(key): count for key, count in list(duplicates.items())[:20]},
            duplicate_key_count=len(duplicates),
        )

    alias_clusters = {
        key: sorted(names) for key, names in loose_names.items() if key and len(names) > 1
    }
    if alias_clusters:
        finding(
            findings,
            "warn",
            "team.possible_aliases",
            "Team names differ only by punctuation/common club suffixes.",
            path,
            aliases=dict(list(alias_clusters.items())[:20]),
        )

    result.teams = len(teams)
    result.odds_group_coverage = dict(sorted(group_coverage.items()))
    if dates:
        result.date_min = min(dates).isoformat()
        result.date_max = max(dates).isoformat()
    if manifest_entry:
        expected_teams = manifest_value(manifest_entry, "teams", "team_count")
        if expected_teams is not None and int(expected_teams) != len(teams):
            finding(
                findings,
                "fail",
                "provenance.team_count_mismatch",
                "Observed team count differs from the manifest.",
                path,
                expected=int(expected_teams),
                actual=len(teams),
            )
        for manifest_key, actual, label in (
            ("first_match_date", result.date_min, "first"),
            ("last_match_date", result.date_max, "last"),
        ):
            expected = manifest_entry.get(manifest_key)
            if expected and str(expected) != actual:
                finding(
                    findings,
                    "fail",
                    "provenance.date_range_mismatch",
                    f"Observed {label} match date differs from the manifest.",
                    path,
                    expected=str(expected),
                    actual=actual,
                )

    if require_complete and result.league:
        expected_teams = expected_team_count(
            result.league, season_info[0] if season_info else None
        )
        if expected_teams is not None and len(teams) != expected_teams:
            finding(
                findings,
                "fail",
                "coverage.team_count",
                "Team count does not match the expected top-flight format.",
                path,
                expected=expected_teams,
                actual=len(teams),
            )
        expected_matches = len(teams) * (len(teams) - 1)
        if len(rows) != expected_matches:
            finding(
                findings,
                "fail",
                "coverage.round_robin_match_count",
                "Completed top-flight season is not a full double round robin.",
                path,
                expected=expected_matches,
                actual=len(rows),
                teams=len(teams),
            )
        expected_per_team = 2 * (len(teams) - 1)
        bad_team_counts = {
            team: appearances[team]
            for team in sorted(teams)
            if appearances[team] != expected_per_team
        }
        if bad_team_counts:
            finding(
                findings,
                "fail",
                "coverage.team_appearance_count",
                "At least one team has an incomplete season schedule.",
                path,
                expected_per_team=expected_per_team,
                actual=bad_team_counts,
            )
        expected_home_away = len(teams) - 1
        bad_home_away = {
            team: {"home": home_appearances[team], "away": away_appearances[team]}
            for team in sorted(teams)
            if home_appearances[team] != expected_home_away
            or away_appearances[team] != expected_home_away
        }
        if bad_home_away:
            finding(
                findings,
                "fail",
                "coverage.home_away_balance",
                "At least one team lacks the expected home/away fixture count.",
                path,
                expected_each=expected_home_away,
                actual=bad_home_away,
            )

    if result.rows:
        closing_rate = result.closing_1x2_rows / result.rows
        if season_info and season_info[0] >= 2019 and closing_rate < 0.9:
            finding(
                findings,
                "warn",
                "odds.closing_coverage_low",
                "Closing 1X2 coverage is below 90% for a post-2019/20 season.",
                path,
                coverage=round(closing_rate, 6),
                rows=result.rows,
            )

    if dates and max(dates) >= ODDS_CAVEAT_DATE:
        pinnacle_columns = [column for column in fieldnames if column.startswith("PS")]
        if pinnacle_columns:
            finding(
                findings,
                "warn",
                "odds.pinnacle_source_caveat",
                "football-data.co.uk warns Pinnacle odds may be stale from 2025-07-23 onward.",
                path,
                affected_from=ODDS_CAVEAT_DATE.isoformat(),
                columns=pinnacle_columns,
            )

    if closing_groups:
        finding(
            findings,
            "warn",
            "leakage.closing_odds_no_capture_time",
            "CSV closing odds have no per-row capture timestamp. They are valid as a market benchmark/label, "
            "but must not be treated as known at an earlier pre-match as_of time.",
            path,
        )

    result.deterministic_sample = deterministic_sample(parsed_rows, sample_size)
    return result, parsed_rows


def load_reference(path: Path) -> list[dict[str, str]]:
    if path.suffix.casefold() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            payload = payload.get("matches", payload.get("fixtures", []))
        if not isinstance(payload, list):
            raise ValueError("Reference JSON must be a list or contain matches/fixtures")
        return [{str(key): str(value) for key, value in row.items()} for row in payload]
    _, _, rows, _ = read_csv_rows(path)
    return rows


def validate_reference(
    all_rows: list[dict[str, Any]],
    reference_path: Path | None,
    findings: list[Finding],
) -> dict[str, Any]:
    if reference_path is None:
        finding(
            findings,
            "warn",
            "authenticity.independent_reference_missing",
            "No independently sourced reference sample was supplied. Internal consistency is not proof of truth.",
        )
        return {"status": "not_run", "matched": 0, "mismatched": 0}
    try:
        references = load_reference(reference_path)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        finding(
            findings,
            "fail",
            "authenticity.reference_parse_failed",
            str(error),
            reference_path,
        )
        return {"status": "failed", "matched": 0, "mismatched": 0}

    index: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in all_rows:
        key = (
            row.get("Div", "").strip(),
            row.get("_parsed_date", ""),
            normalize_key(row.get("HomeTeam", "")),
            normalize_key(row.get("AwayTeam", "")),
        )
        index[key] = row

    matched = 0
    mismatched = 0
    missing = 0
    for position, reference in enumerate(references, start=1):
        source_url = reference.get("SourceURL") or reference.get("source_url") or ""
        if not source_url.startswith("https://"):
            finding(
                findings,
                "fail",
                "authenticity.reference_url_missing",
                "Independent reference row needs an HTTPS source URL.",
                reference_path,
                position + 1,
            )
        try:
            reference_date = parse_match_date(reference.get("Date", ""))
        except ValueError as error:
            finding(
                findings,
                "fail",
                "authenticity.reference_date_invalid",
                str(error),
                reference_path,
                position + 1,
            )
            continue
        key = (
            reference.get("Div", "").strip(),
            reference_date.isoformat(),
            normalize_key(reference.get("HomeTeam", "")),
            normalize_key(reference.get("AwayTeam", "")),
        )
        candidate = index.get(key)
        if candidate is None:
            missing += 1
            finding(
                findings,
                "fail",
                "authenticity.reference_match_missing",
                "Reference match is absent from the audited dataset.",
                reference_path,
                position + 1,
                key=key,
                source_url=source_url,
            )
            continue
        expected_score = (reference.get("FTHG", "").strip(), reference.get("FTAG", "").strip())
        actual_score = (candidate.get("FTHG", "").strip(), candidate.get("FTAG", "").strip())
        if expected_score != actual_score:
            mismatched += 1
            finding(
                findings,
                "fail",
                "authenticity.reference_score_mismatch",
                "Audited score differs from the independent source.",
                reference_path,
                position + 1,
                key=key,
                expected=expected_score,
                actual=actual_score,
                source_url=source_url,
            )
        else:
            matched += 1
    status = "pass" if references and not mismatched and not missing else "fail"
    return {
        "status": status,
        "reference_rows": len(references),
        "matched": matched,
        "mismatched": mismatched,
        "missing": missing,
        "reference_sha256": sha256_file(reference_path),
    }


def validate_crosscheck_report(
    path: Path,
    expected_rows: int,
    findings: list[Finding],
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        finding(
            findings,
            "fail",
            "authenticity.crosscheck_report_invalid",
            str(error),
            path,
        )
        return {"status": "failed", "method": "full_cross_source_report"}
    summary = payload.get("summary") or {}
    confirmed = summary.get("independent_scores_confirmed")
    unresolved = summary.get("unresolved_scores")
    if (
        payload.get("status") not in {"pass", "warn"}
        or confirmed != expected_rows
        or unresolved != 0
    ):
        finding(
            findings,
            "fail",
            "authenticity.crosscheck_report_failed",
            "Cross-source report does not confirm every audited score.",
            path,
            report_status=payload.get("status"),
            expected_rows=expected_rows,
            confirmed=confirmed,
            unresolved=unresolved,
        )
        status = "fail"
    else:
        status = "pass"
        if payload.get("status") == "warn":
            finding(
                findings,
                "warn",
                "authenticity.crosscheck_report_warnings",
                "All full-time scores are independently confirmed, but the report contains separately reviewed non-full-time warnings.",
                path,
                report_findings=summary.get("findings"),
            )
    return {
        "status": status,
        "method": "full_cross_source_report",
        "report_path": str(path.resolve()),
        "report_sha256": sha256_file(path),
        "summary": summary,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="Directory containing immutable source CSV files")
    parser.add_argument("--manifest", help="JSON download manifest; auto-detected when omitted")
    parser.add_argument(
        "--season",
        help="European season, for example 2024/2025, 2024-25, or 2425",
    )
    parser.add_argument(
        "--expected-leagues",
        default=",".join(DEFAULT_LEAGUES),
        help="Comma-separated Div codes required for this audit",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Require a completed double round-robin season and no incomplete/future rows",
    )
    parser.add_argument(
        "--reference",
        help="Independent reference CSV/JSON with Div, Date, HomeTeam, AwayTeam, FTHG, FTAG, SourceURL",
    )
    parser.add_argument(
        "--crosscheck-report",
        help="Full report emitted by crosscheck_historical_results.py",
    )
    parser.add_argument("--sample-size", type=int, default=3)
    parser.add_argument("--output", help="Write the JSON report to this path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve()
    findings: list[Finding] = []

    if not root.is_dir():
        print(f"Audit root is not a directory: {root}", file=sys.stderr)
        return 2
    try:
        season_info = parse_season(args.season)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    manifest_path = discover_manifest(root, args.manifest)
    if manifest_path is None:
        finding(
            findings,
            "fail",
            "provenance.manifest_missing",
            "A download manifest is mandatory; SHA-256 alone cannot prove source provenance.",
        )
        manifest_entries: dict[str, dict[str, Any]] = {}
        manifest_hash = None
    else:
        try:
            manifest_entries, manifest_hash = load_manifest(manifest_path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            finding(
                findings,
                "fail",
                "provenance.manifest_parse_failed",
                str(error),
                manifest_path,
            )
            manifest_entries = {}
            manifest_hash = None

    expected_leagues = {
        token.strip().upper()
        for token in args.expected_leagues.split(",")
        if token.strip()
    }
    csv_files = sorted(path for path in root.rglob("*.csv") if path.is_file())
    if not csv_files:
        finding(findings, "fail", "coverage.no_csv_files", "No CSV files were found.", root)

    file_audits: list[FileAudit] = []
    all_rows: list[dict[str, Any]] = []
    observed_leagues: Counter[str] = Counter()
    file_hashes: dict[str, list[str]] = defaultdict(list)
    global_match_keys: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)

    for path in csv_files:
        audit, rows = audit_file(
            path=path,
            manifest_entry=manifest_entries.get(path.name.casefold()),
            season_info=season_info,
            require_complete=args.require_complete,
            sample_size=max(0, args.sample_size),
            findings=findings,
        )
        file_audits.append(audit)
        all_rows.extend(rows)
        file_hashes[audit.sha256].append(audit.path)
        if audit.league:
            observed_leagues[audit.league.upper()] += 1
        for row in rows:
            key = (
                row.get("Div", "").strip().upper(),
                row.get("_parsed_date", ""),
                normalize_key(row.get("HomeTeam", "")),
                normalize_key(row.get("AwayTeam", "")),
            )
            global_match_keys[key].append(str(path.resolve()))

    audited_csv_names = {path.name.casefold() for path in csv_files}
    for filename, entry in manifest_entries.items():
        attachment = root / filename
        if filename not in audited_csv_names and attachment.is_file():
            validate_manifest_entry(
                attachment,
                entry,
                sha256_file(attachment),
                findings,
            )

    missing_leagues = sorted(expected_leagues - set(observed_leagues))
    extra_leagues = sorted(set(observed_leagues) - expected_leagues)
    if missing_leagues:
        finding(
            findings,
            "fail",
            "coverage.expected_leagues_missing",
            "One or more required leagues are absent.",
            missing=missing_leagues,
            observed=sorted(observed_leagues),
        )
    if extra_leagues:
        finding(
            findings,
            "warn",
            "coverage.unexpected_leagues",
            "Files include leagues outside this audit scope.",
            extra=extra_leagues,
        )
    duplicate_league_files = {
        league: count for league, count in observed_leagues.items() if count > 1
    }
    if duplicate_league_files:
        finding(
            findings,
            "fail",
            "coverage.multiple_files_per_league",
            "This season audit has multiple CSV files for the same Div code.",
            leagues=duplicate_league_files,
        )

    duplicate_content = {digest: paths for digest, paths in file_hashes.items() if len(paths) > 1}
    if duplicate_content:
        finding(
            findings,
            "fail",
            "provenance.duplicate_file_content",
            "Different CSV paths contain identical bytes.",
            duplicates=duplicate_content,
        )

    cross_file_duplicates = {
        "|".join(key): paths
        for key, paths in global_match_keys.items()
        if len(set(paths)) > 1
    }
    if cross_file_duplicates:
        finding(
            findings,
            "fail",
            "match.cross_file_duplicates",
            "The same match identity appears in multiple source files.",
            duplicates=dict(list(cross_file_duplicates.items())[:20]),
        )

    if args.crosscheck_report:
        reference_result = validate_crosscheck_report(
            Path(args.crosscheck_report).resolve(),
            expected_rows=len(all_rows),
            findings=findings,
        )
    else:
        reference_result = validate_reference(
            all_rows,
            Path(args.reference).resolve() if args.reference else None,
            findings,
        )

    counts = Counter(item.severity for item in findings)
    status = "fail" if counts["fail"] else "warn" if counts["warn"] else "pass"
    if reference_result["status"] == "pass":
        authenticity_verdict = (
            "full_independent_score_verification_passed"
            if reference_result.get("method") == "full_cross_source_report"
            else "sampled_independent_verification_passed"
        )
    elif reference_result["status"] == "not_run":
        authenticity_verdict = "internal_checks_only_not_independently_verified"
    else:
        authenticity_verdict = "independent_verification_failed"

    finding_codes = {item.code for item in findings}
    component_verdicts = {
        "provenance_fixity": (
            "fail"
            if any(code.startswith("provenance.") for code in finding_codes)
            else "pass"
        ),
        "match_identity_and_fulltime_results": (
            "fail"
            if any(
                code.startswith(prefix)
                for code in finding_codes
                for prefix in ("coverage.", "date.", "match.", "team.", "result.score_", "result.invalid_")
            )
            else "pass_with_preserved_null_halftime"
            if "result.halftime_missing" in finding_codes
            else "pass"
        ),
        "odds": (
            "fail_quarantine_invalid_cells"
            if any(code.startswith("odds.") and item.severity == "fail" for item in findings for code in (item.code,))
            else "pass_with_coverage_warnings"
            if any(code.startswith("odds.") for code in finding_codes)
            else "pass"
        ),
        "point_in_time_safety": (
            "restricted_closing_odds_not_feature_safe"
            if "leakage.closing_odds_no_capture_time" in finding_codes
            else "pass"
        ),
        "independent_score_verification": reference_result["status"],
    }

    report = {
        "audit_version": AUDIT_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "authenticity_verdict": authenticity_verdict,
        "component_verdicts": component_verdicts,
        "policy": {
            "synthetic_data_allowed": False,
            "missing_value_imputation_allowed": False,
            "raw_files_modified": False,
            "closing_odds_point_in_time_safe": False,
        },
        "scope": {
            "root": str(root),
            "season": season_info[2] if season_info else None,
            "expected_leagues": sorted(expected_leagues),
            "require_complete": args.require_complete,
        },
        "manifest": {
            "path": str(manifest_path.resolve()) if manifest_path else None,
            "sha256": manifest_hash,
            "entries": len(manifest_entries),
        },
        "summary": {
            "files": len(file_audits),
            "rows": sum(item.rows for item in file_audits),
            "completed_rows": sum(item.completed_rows for item in file_audits),
            "incomplete_rows": sum(item.incomplete_rows for item in file_audits),
            "findings": dict(counts),
        },
        "independent_reference": reference_result,
        "files": [asdict(item) for item in file_audits],
        "findings": [asdict(item) for item in findings],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 2 if status == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
