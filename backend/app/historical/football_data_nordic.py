from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from .football_data_couk import (
    DownloadedSourceFile,
    HistoricalDataValidationError,
    HistoricalMatchRecord,
    HistoricalOddsQuote,
    LeagueDefinition,
    ParsedHistoricalFile,
    SeasonDefinition,
    SourceFileSpec,
    normalize_team_name,
)


PARSER_VERSION = "football-data-nordic-v1"
BASE_URL = "https://www.football-data.co.uk/new"
PINNACLE_WARNING_DATE = date(2025, 7, 23)
COMBINED_SEASON = SeasonDefinition("combined", "calendar-2012-current")

NORDIC_LEAGUES: tuple[LeagueDefinition, ...] = (
    LeagueDefinition("FIN", "FI1", "芬兰超级联赛", "FI", "unknown"),
    LeagueDefinition("NOR", "NO1", "挪威超级联赛", "NO", "unknown"),
    LeagueDefinition("SWE", "SE1", "瑞典超级联赛", "SE", "unknown"),
)

EXPECTED_SOURCE_VALUES = {
    "FIN": ("Finland", "Veikkausliiga"),
    "NOR": ("Norway", "Eliteserien"),
    "SWE": ("Sweden", "Allsvenskan"),
}

NORDIC_SPECS: tuple[SourceFileSpec, ...] = tuple(
    SourceFileSpec(
        league=league,
        season=COMBINED_SEASON,
        url_override=f"{BASE_URL}/{league.source_division}.csv",
    )
    for league in NORDIC_LEAGUES
)


@dataclass(frozen=True)
class _ClosingOddsSpec:
    bookmaker: str
    home: str
    draw: str
    away: str


_CLOSING_ODDS: tuple[_ClosingOddsSpec, ...] = (
    _ClosingOddsSpec("Pinnacle", "PSCH", "PSCD", "PSCA"),
    _ClosingOddsSpec("MarketMax", "MaxCH", "MaxCD", "MaxCA"),
    _ClosingOddsSpec("MarketAverage", "AvgCH", "AvgCD", "AvgCA"),
    _ClosingOddsSpec("BetfairExchange", "BFECH", "BFECD", "BFECA"),
    _ClosingOddsSpec("Bet365", "B365CH", "B365CD", "B365CA"),
)

_REQUIRED_HEADERS = {
    "Country", "League", "Season", "Date", "Time", "Home", "Away",
    "HG", "AG", "Res", "PSCH", "PSCD", "PSCA", "MaxCH", "MaxCD",
    "MaxCA", "AvgCH", "AvgCD", "AvgCA", "BFECH", "BFECD", "BFECA",
    "B365CH", "B365CD", "B365CA",
}


class FootballDataNordicAdapter:
    name = "football-data.co.uk"
    parser_version = PARSER_VERSION

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root

    async def download(
        self,
        client: httpx.AsyncClient,
        spec: SourceFileSpec,
        *,
        force: bool = False,
    ) -> DownloadedSourceFile:
        target = self.data_root / "new" / f"{spec.league.source_division}.csv"
        target.parent.mkdir(parents=True, exist_ok=True)
        response = await client.get(
            spec.url,
            headers={
                "Accept": "text/csv,text/plain;q=0.9",
                "User-Agent": "zucai-research/0.1 (auditable Nordic historical import; low frequency)",
            },
            follow_redirects=True,
        )
        response.raise_for_status()
        content = response.content
        self._validate_download(spec, content, response.headers.get("content-type"))
        digest = hashlib.sha256(content).hexdigest()
        if force or not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            temporary = target.with_suffix(".csv.part")
            temporary.write_bytes(content)
            os.replace(temporary, target)
        return DownloadedSourceFile(
            spec=spec,
            path=target,
            content_sha256=digest,
            content_bytes=len(content),
            http_etag=response.headers.get("etag"),
            http_last_modified=response.headers.get("last-modified"),
            retrieved_from_network=True,
        )

    @staticmethod
    def _validate_download(
        spec: SourceFileSpec,
        content: bytes,
        content_type: str | None,
    ) -> None:
        if not 100 <= len(content) <= 20 * 1024 * 1024:
            raise HistoricalDataValidationError(
                f"{spec.url} returned an unsafe content length"
            )
        first_line = content.splitlines()[0].decode("ascii", errors="ignore")
        headers = set(next(csv.reader([first_line])))
        missing = sorted(_REQUIRED_HEADERS - headers)
        if missing:
            raise HistoricalDataValidationError(
                f"{spec.url} is missing required columns: {missing}"
            )
        if content_type and "csv" not in content_type.lower() and "text/plain" not in content_type.lower():
            raise HistoricalDataValidationError(
                f"{spec.url} returned unexpected content type {content_type}"
            )

    def local_file(self, spec: SourceFileSpec) -> DownloadedSourceFile:
        path = self.data_root / "new" / f"{spec.league.source_division}.csv"
        if not path.is_file():
            raise FileNotFoundError(f"offline Nordic source file does not exist: {path}")
        content = path.read_bytes()
        self._validate_download(spec, content, "text/csv")
        return DownloadedSourceFile(
            spec=spec,
            path=path,
            content_sha256=hashlib.sha256(content).hexdigest(),
            content_bytes=len(content),
            http_etag=None,
            http_last_modified=None,
            retrieved_from_network=False,
        )

    def parse(self, downloaded: DownloadedSourceFile) -> ParsedHistoricalFile:
        content = downloaded.path.read_bytes()
        text, encoding = _decode_csv(content)
        reader = csv.DictReader(text.splitlines())
        if reader.fieldnames is None:
            raise HistoricalDataValidationError(f"{downloaded.path} has no header")

        expected_country, expected_league = EXPECTED_SOURCE_VALUES[
            downloaded.spec.league.source_division
        ]
        records: list[HistoricalMatchRecord] = []
        rejected: list[dict[str, Any]] = []
        natural_keys: set[tuple[Any, ...]] = set()
        seasons: set[str] = set()
        teams: set[str] = set()
        odds_counts: dict[str, int] = {}
        incomplete_quote_groups = 0
        invalid_quote_groups = 0
        pinnacle_quarantined = 0
        market_consistency_quarantined = 0
        anomaly_samples: list[dict[str, Any]] = []
        input_rows = 0

        for row_number, source_row in enumerate(reader, start=2):
            raw_row = {
                str(key): value or ""
                for key, value in source_row.items()
                if key is not None
            }
            if not any(value.strip() for value in raw_row.values()):
                continue
            input_rows += 1
            row = {key.strip(): value.strip() for key, value in raw_row.items()}
            try:
                if row.get("Country") != expected_country or row.get("League") != expected_league:
                    raise HistoricalDataValidationError(
                        f"unexpected country/league {row.get('Country')!r}/{row.get('League')!r}"
                    )
                season_label = _required(row, "Season")
                if not season_label.isdigit() or len(season_label) != 4:
                    raise HistoricalDataValidationError(f"invalid calendar season {season_label!r}")
                match_date = _parse_date(_required(row, "Date"))
                if match_date.year != int(season_label):
                    raise HistoricalDataValidationError(
                        f"date {match_date.isoformat()} does not belong to season {season_label}"
                    )
                home_team = _required(row, "Home")
                away_team = _required(row, "Away")
                home_norm = normalize_team_name(home_team)
                away_norm = normalize_team_name(away_team)
                if not home_norm or not away_norm or home_norm == away_norm:
                    raise HistoricalDataValidationError("invalid or identical normalized teams")
                home_goals = _nonnegative_int(_required(row, "HG"), "HG")
                away_goals = _nonnegative_int(_required(row, "AG"), "AG")
                expected_result = (
                    "H" if home_goals > away_goals else
                    "A" if home_goals < away_goals else "D"
                )
                result = _required(row, "Res").upper()
                if result != expected_result:
                    raise HistoricalDataValidationError(
                        f"Res={result!r} conflicts with score {home_goals}-{away_goals}"
                    )
                odds, odds_audit = _parse_closing_odds(row, match_date)
                incomplete_quote_groups += odds_audit["incomplete"]
                invalid_quote_groups += odds_audit["invalid"]
                pinnacle_quarantined += odds_audit["pinnacle_quarantined"]
                market_consistency_quarantined += odds_audit["market_consistency_quarantined"]
                if odds_audit["anomaly"]:
                    anomaly_samples.append({
                        "row": row_number,
                        "date": match_date.isoformat(),
                        "home": home_team,
                        "away": away_team,
                        **odds_audit["anomaly"],
                    })

                natural_key = (
                    downloaded.spec.league.source_division,
                    season_label,
                    match_date,
                    home_norm,
                    away_norm,
                )
                if natural_key in natural_keys:
                    raise HistoricalDataValidationError("duplicate natural match key")
                natural_keys.add(natural_key)
                row_hash = hashlib.sha256(
                    json.dumps(
                        raw_row,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                match_time = _parse_time(row.get("Time", ""))
                record = HistoricalMatchRecord(
                    competition_code=downloaded.spec.league.competition_code,
                    source_division=downloaded.spec.league.source_division,
                    season_label=season_label,
                    match_date=match_date,
                    match_time_local=match_time,
                    source_timezone="unknown",
                    home_team=home_team,
                    away_team=away_team,
                    home_team_norm=home_norm,
                    away_team_norm=away_norm,
                    home_goals=home_goals,
                    away_goals=away_goals,
                    full_time_result=result,
                    ht_home_goals=None,
                    ht_away_goals=None,
                    half_time_result=None,
                    source_row_number=row_number,
                    source_row_hash=row_hash,
                    match_stats={},
                    raw_row=raw_row,
                    odds=odds,
                    quality_flags=(
                        ("source_time_timezone_unverified",) if match_time else ()
                    ),
                )
                records.append(record)
                seasons.add(season_label)
                teams.update((home_team, away_team))
                for quote in odds:
                    odds_counts[quote.bookmaker] = odds_counts.get(quote.bookmaker, 0) + 1
            except (HistoricalDataValidationError, ValueError) as error:
                rejected.append({"row": row_number, "reason": str(error)[:240]})

        if not records:
            raise HistoricalDataValidationError(
                f"{downloaded.path} contained no valid completed Nordic matches"
            )
        latest_season = max(seasons)
        records = [
            replace(
                record,
                quality_flags=record.quality_flags + ("partial_season_snapshot",),
            )
            if record.season_label == latest_season
            else record
            for record in records
        ]
        report = {
            "source": self.name,
            "source_url": downloaded.spec.url,
            "parser_version": PARSER_VERSION,
            "encoding": encoding,
            "sha256": downloaded.content_sha256,
            "content_bytes": downloaded.content_bytes,
            "input_rows": input_rows,
            "valid_matches": len(records),
            "rejected_count": len(rejected),
            "rejected_rows": rejected[:100],
            "distinct_teams": len(teams),
            "seasons": sorted(seasons),
            "latest_season_is_partial_snapshot": latest_season,
            "odds_quote_counts": odds_counts,
            "incomplete_quote_groups_quarantined": incomplete_quote_groups,
            "invalid_quote_groups_quarantined": invalid_quote_groups,
            "pinnacle_quote_groups_quarantined": pinnacle_quarantined,
            "market_consistency_quote_groups_quarantined": market_consistency_quarantined,
            "anomaly_samples": anomaly_samples[:100],
            "source_timezone": "unknown",
            "warnings": [
                "source Time has no documented timezone; stored as unknown and never converted",
                f"calendar season {latest_season} is a live partial snapshot",
                "Pinnacle quotes dated 2025-07-23 or later are quarantined",
            ],
        }
        return ParsedHistoricalFile(
            downloaded=downloaded,
            records=tuple(records),
            validation_report=report,
        )


def _parse_closing_odds(
    row: dict[str, str],
    match_date: date,
) -> tuple[tuple[HistoricalOddsQuote, ...], dict[str, Any]]:
    quotes: list[HistoricalOddsQuote] = []
    incomplete = 0
    invalid = 0
    pinnacle_quarantined = 0
    market_consistency_quarantined = 0
    anomaly: dict[str, Any] | None = None

    max_values = [_decimal(row.get(key, "")) for key in ("MaxCH", "MaxCD", "MaxCA")]
    avg_values = [_decimal(row.get(key, "")) for key in ("AvgCH", "AvgCD", "AvgCA")]
    inconsistent_market = (
        all(value is not None for value in max_values + avg_values)
        and any(avg > maximum for avg, maximum in zip(avg_values, max_values, strict=True))
    )
    if inconsistent_market:
        anomaly = {
            "reason": "market_average_exceeds_market_maximum",
            "source_values": {
                key: row.get(key, "")
                for key in ("MaxCH", "MaxCD", "MaxCA", "AvgCH", "AvgCD", "AvgCA")
            },
        }

    for spec in _CLOSING_ODDS:
        raw_values = [row.get(spec.home, ""), row.get(spec.draw, ""), row.get(spec.away, "")]
        present = [bool(value.strip()) for value in raw_values]
        if not any(present):
            continue
        if not all(present):
            incomplete += 1
            continue
        values = [_decimal(value) for value in raw_values]
        if any(value is None or value <= 1 for value in values):
            invalid += 1
            continue
        if spec.bookmaker == "Pinnacle" and match_date >= PINNACLE_WARNING_DATE:
            pinnacle_quarantined += 1
            continue
        if inconsistent_market and spec.bookmaker in {"MarketMax", "MarketAverage"}:
            market_consistency_quarantined += 1
            continue
        quotes.append(
            HistoricalOddsQuote(
                bookmaker=spec.bookmaker,
                market_type="1X2",
                is_closing=True,
                price_timing="closing",
                point_in_time_safe=False,
                line_value=None,
                odd_home=values[0],
                odd_draw=values[1],
                odd_away=values[2],
                source_columns={
                    spec.home: raw_values[0],
                    spec.draw: raw_values[1],
                    spec.away: raw_values[2],
                },
            )
        )
    return tuple(quotes), {
        "incomplete": incomplete,
        "invalid": invalid,
        "pinnacle_quarantined": pinnacle_quarantined,
        "market_consistency_quarantined": market_consistency_quarantined,
        "anomaly": anomaly,
    }


def _decode_csv(content: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise HistoricalDataValidationError("CSV is neither UTF-8 nor Windows-1252")


def _required(row: dict[str, str], key: str) -> str:
    value = (row.get(key) or "").strip()
    if not value:
        raise HistoricalDataValidationError(f"missing required value {key}")
    return value


def _parse_date(value: str) -> date:
    for pattern in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(value.strip(), pattern).date()
        except ValueError:
            continue
    raise HistoricalDataValidationError(f"invalid match date {value!r}")


def _parse_time(value: str) -> time | None:
    raw = value.strip()
    if not raw:
        return None
    for pattern in ("%H:%M", "%H.%M"):
        try:
            return datetime.strptime(raw, pattern).time()
        except ValueError:
            continue
    raise HistoricalDataValidationError(f"invalid match time {value!r}")


def _nonnegative_int(value: str, field_name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise HistoricalDataValidationError(f"{field_name} is not an integer") from error
    if parsed < 0:
        raise HistoricalDataValidationError(f"{field_name} must be non-negative")
    return parsed


def _decimal(value: str) -> Decimal | None:
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = Decimal(raw)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None
