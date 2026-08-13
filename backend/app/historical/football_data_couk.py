from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import httpx


SOURCE_NAME = "football-data.co.uk"
PARSER_VERSION = "football-data-couk-v2"
BASE_URL = "https://www.football-data.co.uk/mmz4281"


@dataclass(frozen=True)
class LeagueDefinition:
    source_division: str
    competition_code: str
    name: str
    country_code: str
    timezone: str


@dataclass(frozen=True)
class SeasonDefinition:
    source_code: str
    label: str


@dataclass(frozen=True)
class SourceFileSpec:
    league: LeagueDefinition
    season: SeasonDefinition
    url_override: str | None = None

    @property
    def url(self) -> str:
        return self.url_override or (
            f"{BASE_URL}/{self.season.source_code}/{self.league.source_division}.csv"
        )


BIG_FIVE_LEAGUES: tuple[LeagueDefinition, ...] = (
    LeagueDefinition("E0", "PL", "英格兰超级联赛", "GB", "Europe/London"),
    # Cross-source verification against local-kickoff feeds shows the CSV Time
    # column is expressed in UK local time for continental leagues as well.
    # It must not be interpreted as the venue country's timezone.
    LeagueDefinition("SP1", "PD", "西班牙甲级联赛", "ES", "Europe/London"),
    LeagueDefinition("D1", "BL1", "德国甲级联赛", "DE", "Europe/London"),
    LeagueDefinition("I1", "SA", "意大利甲级联赛", "IT", "Europe/London"),
    LeagueDefinition("F1", "FL1", "法国甲级联赛", "FR", "Europe/London"),
)

DEFAULT_SEASONS: tuple[SeasonDefinition, ...] = (
    SeasonDefinition("2122", "2021/2022"),
    SeasonDefinition("2223", "2022/2023"),
    SeasonDefinition("2324", "2023/2024"),
    SeasonDefinition("2425", "2024/2025"),
    SeasonDefinition("2526", "2025/2026"),
)


def season_from_code(source_code: str) -> SeasonDefinition:
    code = source_code.strip()
    if not re.fullmatch(r"\d{4}", code):
        raise ValueError(f"invalid season code {source_code!r}; expected four digits such as 2425")
    start_short = int(code[:2])
    end_short = int(code[2:])
    start_year = (1900 if start_short >= 90 else 2000) + start_short
    end_year = start_year + 1
    if end_year % 100 != end_short:
        raise ValueError(f"season code {source_code!r} does not describe consecutive years")
    if not 1993 <= start_year <= 2025:
        raise ValueError(
            f"season code {source_code!r} is outside the verified Football-Data range "
            "1993/94 through 2025/26"
        )
    return SeasonDefinition(code, f"{start_year:04d}/{end_year:04d}")


def all_verified_seasons() -> tuple[SeasonDefinition, ...]:
    return tuple(
        season_from_code(f"{year % 100:02d}{(year + 1) % 100:02d}")
        for year in range(1993, 2026)
    )


@dataclass(frozen=True)
class HistoricalOddsQuote:
    bookmaker: str
    market_type: str
    is_closing: bool
    price_timing: str
    point_in_time_safe: bool
    line_value: Decimal | None
    odd_home: Decimal | None = None
    odd_draw: Decimal | None = None
    odd_away: Decimal | None = None
    odd_over: Decimal | None = None
    odd_under: Decimal | None = None
    source_columns: dict[str, str] = field(default_factory=dict)
    quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class HistoricalMatchRecord:
    competition_code: str
    source_division: str
    season_label: str
    match_date: date
    match_time_local: time | None
    source_timezone: str
    home_team: str
    away_team: str
    home_team_norm: str
    away_team_norm: str
    home_goals: int
    away_goals: int
    full_time_result: str
    ht_home_goals: int | None
    ht_away_goals: int | None
    half_time_result: str | None
    source_row_number: int
    source_row_hash: str
    match_stats: dict[str, Any]
    raw_row: dict[str, str]
    odds: tuple[HistoricalOddsQuote, ...]
    quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class DownloadedSourceFile:
    spec: SourceFileSpec
    path: Path
    content_sha256: str
    content_bytes: int
    http_etag: str | None
    http_last_modified: str | None
    retrieved_from_network: bool = True


@dataclass(frozen=True)
class ParsedHistoricalFile:
    downloaded: DownloadedSourceFile
    records: tuple[HistoricalMatchRecord, ...]
    validation_report: dict[str, Any]


@dataclass(frozen=True)
class _OddsSpec:
    bookmaker: str
    market_type: str
    is_closing: bool
    columns: dict[str, str]
    line_column: str | None = None


_ODDS_SPECS: tuple[_OddsSpec, ...] = (
    # Legacy bookmaker columns are named exactly as documented in the
    # provider's notes.txt. They are opening/pre-closing observations because
    # those files do not carry the later "C" closing-column convention.
    _OddsSpec("Betfair", "1X2", False, {"odd_home": "BFH", "odd_draw": "BFD", "odd_away": "BFA"}),
    _OddsSpec("Betfred", "1X2", False, {"odd_home": "BFDH", "odd_draw": "BFDD", "odd_away": "BFDA"}),
    _OddsSpec("BetMGM", "1X2", False, {"odd_home": "BMGMH", "odd_draw": "BMGMD", "odd_away": "BMGMA"}),
    _OddsSpec("BetVictor", "1X2", False, {"odd_home": "BVH", "odd_draw": "BVD", "odd_away": "BVA"}),
    _OddsSpec("Blue Square", "1X2", False, {"odd_home": "BSH", "odd_draw": "BSD", "odd_away": "BSA"}),
    _OddsSpec("Bet&Win", "1X2", False, {"odd_home": "BWH", "odd_draw": "BWD", "odd_away": "BWA"}),
    _OddsSpec("Coral", "1X2", False, {"odd_home": "CLH", "odd_draw": "CLD", "odd_away": "CLA"}),
    _OddsSpec("Gamebookers", "1X2", False, {"odd_home": "GBH", "odd_draw": "GBD", "odd_away": "GBA"}),
    _OddsSpec("Interwetten", "1X2", False, {"odd_home": "IWH", "odd_draw": "IWD", "odd_away": "IWA"}),
    _OddsSpec("Ladbrokes", "1X2", False, {"odd_home": "LBH", "odd_draw": "LBD", "odd_away": "LBA"}),
    _OddsSpec("PinnacleLegacy", "1X2", False, {"odd_home": "PH", "odd_draw": "PD", "odd_away": "PA"}),
    _OddsSpec("Sporting Odds", "1X2", False, {"odd_home": "SOH", "odd_draw": "SOD", "odd_away": "SOA"}),
    _OddsSpec("Sportingbet", "1X2", False, {"odd_home": "SBH", "odd_draw": "SBD", "odd_away": "SBA"}),
    _OddsSpec("Stan James", "1X2", False, {"odd_home": "SJH", "odd_draw": "SJD", "odd_away": "SJA"}),
    _OddsSpec("Stanleybet", "1X2", False, {"odd_home": "SYH", "odd_draw": "SYD", "odd_away": "SYA"}),
    _OddsSpec("VC Bet", "1X2", False, {"odd_home": "VCH", "odd_draw": "VCD", "odd_away": "VCA"}),
    _OddsSpec("William Hill", "1X2", False, {"odd_home": "WHH", "odd_draw": "WHD", "odd_away": "WHA"}),
    _OddsSpec("BetbrainMax", "1X2", False, {"odd_home": "BbMxH", "odd_draw": "BbMxD", "odd_away": "BbMxA"}),
    _OddsSpec("BetbrainAverage", "1X2", False, {"odd_home": "BbAvH", "odd_draw": "BbAvD", "odd_away": "BbAvA"}),
    _OddsSpec("Bet365", "1X2", False, {"odd_home": "B365H", "odd_draw": "B365D", "odd_away": "B365A"}),
    _OddsSpec("Pinnacle", "1X2", False, {"odd_home": "PSH", "odd_draw": "PSD", "odd_away": "PSA"}),
    _OddsSpec("MarketMax", "1X2", False, {"odd_home": "MaxH", "odd_draw": "MaxD", "odd_away": "MaxA"}),
    _OddsSpec("MarketAverage", "1X2", False, {"odd_home": "AvgH", "odd_draw": "AvgD", "odd_away": "AvgA"}),
    _OddsSpec("BetfairExchange", "1X2", False, {"odd_home": "BFEH", "odd_draw": "BFED", "odd_away": "BFEA"}),
    _OddsSpec("Bet365", "1X2", True, {"odd_home": "B365CH", "odd_draw": "B365CD", "odd_away": "B365CA"}),
    _OddsSpec("Pinnacle", "1X2", True, {"odd_home": "PSCH", "odd_draw": "PSCD", "odd_away": "PSCA"}),
    _OddsSpec("MarketMax", "1X2", True, {"odd_home": "MaxCH", "odd_draw": "MaxCD", "odd_away": "MaxCA"}),
    _OddsSpec("MarketAverage", "1X2", True, {"odd_home": "AvgCH", "odd_draw": "AvgCD", "odd_away": "AvgCA"}),
    _OddsSpec("BetfairExchange", "1X2", True, {"odd_home": "BFECH", "odd_draw": "BFECD", "odd_away": "BFECA"}),
    _OddsSpec("Bet365", "OU25", False, {"odd_over": "B365>2.5", "odd_under": "B365<2.5"}),
    _OddsSpec("Pinnacle", "OU25", False, {"odd_over": "P>2.5", "odd_under": "P<2.5"}),
    _OddsSpec("MarketMax", "OU25", False, {"odd_over": "Max>2.5", "odd_under": "Max<2.5"}),
    _OddsSpec("MarketAverage", "OU25", False, {"odd_over": "Avg>2.5", "odd_under": "Avg<2.5"}),
    _OddsSpec("BetfairExchange", "OU25", False, {"odd_over": "BFE>2.5", "odd_under": "BFE<2.5"}),
    _OddsSpec("Bet365", "OU25", True, {"odd_over": "B365C>2.5", "odd_under": "B365C<2.5"}),
    _OddsSpec("Pinnacle", "OU25", True, {"odd_over": "PC>2.5", "odd_under": "PC<2.5"}),
    _OddsSpec("MarketMax", "OU25", True, {"odd_over": "MaxC>2.5", "odd_under": "MaxC<2.5"}),
    _OddsSpec("MarketAverage", "OU25", True, {"odd_over": "AvgC>2.5", "odd_under": "AvgC<2.5"}),
    _OddsSpec("BetfairExchange", "OU25", True, {"odd_over": "BFEC>2.5", "odd_under": "BFEC<2.5"}),
    _OddsSpec("Gamebookers", "OU25", False, {"odd_over": "GB>2.5", "odd_under": "GB<2.5"}),
    _OddsSpec("BetbrainMax", "OU25", False, {"odd_over": "BbMx>2.5", "odd_under": "BbMx<2.5"}),
    _OddsSpec("BetbrainAverage", "OU25", False, {"odd_over": "BbAv>2.5", "odd_under": "BbAv<2.5"}),
    _OddsSpec("Bet365", "AH", False, {"odd_home": "B365AHH", "odd_away": "B365AHA"}, "AHh"),
    _OddsSpec("Pinnacle", "AH", False, {"odd_home": "PAHH", "odd_away": "PAHA"}, "AHh"),
    _OddsSpec("MarketMax", "AH", False, {"odd_home": "MaxAHH", "odd_away": "MaxAHA"}, "AHh"),
    _OddsSpec("MarketAverage", "AH", False, {"odd_home": "AvgAHH", "odd_away": "AvgAHA"}, "AHh"),
    _OddsSpec("BetfairExchange", "AH", False, {"odd_home": "BFEAHH", "odd_away": "BFEAHA"}, "AHh"),
    _OddsSpec("Bet365", "AH", True, {"odd_home": "B365CAHH", "odd_away": "B365CAHA"}, "AHCh"),
    _OddsSpec("Pinnacle", "AH", True, {"odd_home": "PCAHH", "odd_away": "PCAHA"}, "AHCh"),
    _OddsSpec("MarketMax", "AH", True, {"odd_home": "MaxCAHH", "odd_away": "MaxCAHA"}, "AHCh"),
    _OddsSpec("MarketAverage", "AH", True, {"odd_home": "AvgCAHH", "odd_away": "AvgCAHA"}, "AHCh"),
    _OddsSpec("BetfairExchange", "AH", True, {"odd_home": "BFECAHH", "odd_away": "BFECAHA"}, "AHCh"),
    _OddsSpec("Gamebookers", "AH", False, {"odd_home": "GBAHH", "odd_away": "GBAHA"}, "GBAH"),
    _OddsSpec("Ladbrokes", "AH", False, {"odd_home": "LBAHH", "odd_away": "LBAHA"}, "LBAH"),
    _OddsSpec("BetbrainMax", "AH", False, {"odd_home": "BbMxAHH", "odd_away": "BbMxAHA"}, "BbAHh"),
    _OddsSpec("BetbrainAverage", "AH", False, {"odd_home": "BbAvAHH", "odd_away": "BbAvAHA"}, "BbAHh"),
)

_MATCH_STAT_COLUMNS = (
    "Referee", "HS", "AS", "HST", "AST", "HF", "AF", "HC", "AC", "HY", "AY", "HR", "AR",
)


class HistoricalDataValidationError(RuntimeError):
    pass


def normalize_team_name(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", folded)


def specs_for(
    leagues: Iterable[LeagueDefinition] = BIG_FIVE_LEAGUES,
    seasons: Iterable[SeasonDefinition] = DEFAULT_SEASONS,
) -> tuple[SourceFileSpec, ...]:
    return tuple(SourceFileSpec(league, season) for season in seasons for league in leagues)


class FootballDataCoUkAdapter:
    name = SOURCE_NAME
    parser_version = PARSER_VERSION

    def __init__(self, data_root: Path) -> None:
        self.data_root = data_root

    @staticmethod
    def capabilities() -> set[str]:
        return {"results", "odds_history"}

    async def download(
        self,
        client: httpx.AsyncClient,
        spec: SourceFileSpec,
        *,
        force: bool = False,
    ) -> DownloadedSourceFile:
        target = self.data_root / spec.season.source_code / f"{spec.league.source_division}.csv"
        target.parent.mkdir(parents=True, exist_ok=True)

        response = await client.get(
            spec.url,
            headers={
                "Accept": "text/csv,text/plain;q=0.9",
                "User-Agent": "zucai-research/0.1 (auditable historical import; low frequency)",
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
    def _validate_download(spec: SourceFileSpec, content: bytes, content_type: str | None) -> None:
        if len(content) < 100:
            raise HistoricalDataValidationError(f"{spec.url} returned an unexpectedly small file")
        if len(content) > 20 * 1024 * 1024:
            raise HistoricalDataValidationError(f"{spec.url} exceeded the 20 MiB safety limit")
        first_line = content.splitlines()[0].decode("ascii", errors="ignore")
        required = {"Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"}
        headers = set(next(csv.reader([first_line])))
        missing = sorted(required - headers)
        if missing:
            raise HistoricalDataValidationError(f"{spec.url} is missing required columns: {missing}")
        if content_type and "csv" not in content_type.lower() and "text/plain" not in content_type.lower():
            raise HistoricalDataValidationError(f"{spec.url} returned unexpected content type {content_type}")

    def parse(self, downloaded: DownloadedSourceFile) -> ParsedHistoricalFile:
        text, encoding = _decode_csv(downloaded.path.read_bytes())
        reader = csv.DictReader(text.splitlines())
        if reader.fieldnames is None:
            raise HistoricalDataValidationError(f"{downloaded.path} has no header")

        records: list[HistoricalMatchRecord] = []
        rejected: list[dict[str, Any]] = []
        natural_keys: set[tuple[Any, ...]] = set()
        teams: set[str] = set()
        result_counts = {"H": 0, "D": 0, "A": 0}
        odds_counts: dict[str, int] = {}
        invalid_odds_quotes = 0
        invalid_odds_cells: list[dict[str, Any]] = []
        input_rows = 0

        for row_number, source_row in enumerate(reader, start=2):
            row = {str(key).strip(): (value or "").strip() for key, value in source_row.items() if key}
            if not any(row.values()):
                continue
            input_rows += 1
            try:
                record, invalid_quotes = self._parse_row(downloaded.spec, row_number, row)
                invalid_odds_quotes += invalid_quotes
                invalid_odds_cells.extend(
                    {"row": row_number, **issue}
                    for issue in _invalid_odds_cells(row)
                )
                natural_key = (
                    record.source_division,
                    record.season_label,
                    record.match_date,
                    record.home_team_norm,
                    record.away_team_norm,
                )
                if natural_key in natural_keys:
                    raise HistoricalDataValidationError("duplicate natural match key inside source file")
                natural_keys.add(natural_key)
                records.append(record)
                teams.update((record.home_team, record.away_team))
                result_counts[record.full_time_result] += 1
                for quote in record.odds:
                    key = f"{quote.market_type}:{'closing' if quote.is_closing else 'opening'}"
                    odds_counts[key] = odds_counts.get(key, 0) + 1
            except (HistoricalDataValidationError, ValueError) as error:
                rejected.append({"row": row_number, "reason": str(error)[:240]})

        if not records:
            raise HistoricalDataValidationError(f"{downloaded.path} contained no valid completed matches")

        warnings: list[str] = []
        if len(records) < 250:
            warnings.append("valid match count is below 250; verify league format or partial season")
        if rejected:
            warnings.append("one or more rows were rejected; inspect rejected_rows")
        if invalid_odds_cells:
            warnings.append(
                "source contains odds cells at or below 1; those values are preserved only in raw_row "
                "and excluded from normalized historical_odds"
            )
        if downloaded.spec.season.source_code == "2526":
            warnings.append(
                "football-data.co.uk warns that Pinnacle odds became unreliable after 2025-07-23; "
                "Pinnacle rows are preserved but flagged and must not be used as the sole market benchmark"
            )

        report = {
            "source": SOURCE_NAME,
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
            "result_counts": result_counts,
            "odds_quote_counts": odds_counts,
            "invalid_odds_quotes": invalid_odds_quotes,
            "invalid_odds_cell_count": len(invalid_odds_cells),
            "invalid_odds_cells": invalid_odds_cells[:1000],
            "warnings": warnings,
        }
        return ParsedHistoricalFile(downloaded=downloaded, records=tuple(records), validation_report=report)

    def _parse_row(
        self,
        spec: SourceFileSpec,
        row_number: int,
        row: dict[str, str],
    ) -> tuple[HistoricalMatchRecord, int]:
        if row.get("Div") != spec.league.source_division:
            raise HistoricalDataValidationError(
                f"division mismatch: expected {spec.league.source_division}, got {row.get('Div')!r}"
            )
        match_date = _parse_date(row.get("Date"))
        expected_start_year = int(spec.season.label[:4])
        if match_date.year not in {expected_start_year, expected_start_year + 1}:
            raise HistoricalDataValidationError(
                f"date {match_date.isoformat()} falls outside season {spec.season.label}"
            )

        home_team = _required(row, "HomeTeam")
        away_team = _required(row, "AwayTeam")
        home_team_norm = normalize_team_name(home_team)
        away_team_norm = normalize_team_name(away_team)
        if not home_team_norm or not away_team_norm or home_team_norm == away_team_norm:
            raise HistoricalDataValidationError("invalid or identical normalized team names")

        home_goals = _parse_nonnegative_int(_required(row, "FTHG"), "FTHG")
        away_goals = _parse_nonnegative_int(_required(row, "FTAG"), "FTAG")
        expected_result = "H" if home_goals > away_goals else "A" if home_goals < away_goals else "D"
        full_time_result = _required(row, "FTR").upper()
        if full_time_result != expected_result:
            raise HistoricalDataValidationError(
                f"FTR={full_time_result!r} conflicts with score {home_goals}-{away_goals}"
            )

        ht_home = _optional_nonnegative_int(row.get("HTHG"), "HTHG")
        ht_away = _optional_nonnegative_int(row.get("HTAG"), "HTAG")
        half_result = (row.get("HTR") or "").upper() or None
        if ht_home is not None and ht_away is not None:
            expected_half = "H" if ht_home > ht_away else "A" if ht_home < ht_away else "D"
            if half_result and half_result != expected_half:
                raise HistoricalDataValidationError(
                    f"HTR={half_result!r} conflicts with half-time score {ht_home}-{ht_away}"
                )
            half_result = expected_half
        elif half_result:
            raise HistoricalDataValidationError("HTR is present without both half-time goal columns")

        row_hash = hashlib.sha256(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        match_stats = {key: row[key] for key in _MATCH_STAT_COLUMNS if row.get(key) not in {None, ""}}
        odds, invalid_quotes = _parse_odds(row, spec.season.source_code)

        return HistoricalMatchRecord(
            competition_code=spec.league.competition_code,
            source_division=spec.league.source_division,
            season_label=spec.season.label,
            match_date=match_date,
            match_time_local=_parse_time(row.get("Time")),
            source_timezone=spec.league.timezone,
            home_team=home_team,
            away_team=away_team,
            home_team_norm=home_team_norm,
            away_team_norm=away_team_norm,
            home_goals=home_goals,
            away_goals=away_goals,
            full_time_result=full_time_result,
            ht_home_goals=ht_home,
            ht_away_goals=ht_away,
            half_time_result=half_result,
            source_row_number=row_number,
            source_row_hash=row_hash,
            match_stats=match_stats,
            raw_row=row,
            odds=odds,
        ), invalid_quotes


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


def _parse_date(value: str | None) -> date:
    raw = (value or "").strip()
    for pattern in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
    raise HistoricalDataValidationError(f"invalid match date {raw!r}")


def _parse_time(value: str | None) -> time | None:
    raw = (value or "").strip()
    if not raw:
        return None
    for pattern in ("%H:%M", "%H.%M"):
        try:
            return datetime.strptime(raw, pattern).time()
        except ValueError:
            continue
    raise HistoricalDataValidationError(f"invalid match time {raw!r}")


def _parse_nonnegative_int(value: str, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise HistoricalDataValidationError(f"{field_name} is not an integer") from error
    if parsed < 0:
        raise HistoricalDataValidationError(f"{field_name} must be non-negative")
    return parsed


def _optional_nonnegative_int(value: str | None, field_name: str) -> int | None:
    raw = (value or "").strip()
    return _parse_nonnegative_int(raw, field_name) if raw else None


def _decimal(value: str | None, *, odds: bool) -> Decimal | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = Decimal(raw)
    except InvalidOperation:
        return None
    if not parsed.is_finite():
        return None
    if odds and parsed <= 1:
        return None
    return parsed


def _parse_odds(row: dict[str, str], season_code: str) -> tuple[tuple[HistoricalOddsQuote, ...], int]:
    quotes: list[HistoricalOddsQuote] = []
    invalid = 0
    season = season_from_code(season_code)
    start_year = int(season.label[:4])
    for spec in _ODDS_SPECS:
        raw_values = {target: row.get(column, "") for target, column in spec.columns.items()}
        if not any(value.strip() for value in raw_values.values()):
            continue
        parsed = {target: _decimal(value, odds=True) for target, value in raw_values.items()}
        if any(value is None for value in parsed.values()):
            invalid += 1
            continue
        line_column = spec.line_column
        if (
            spec.bookmaker == "Bet365"
            and spec.market_type == "AH"
            and line_column == "AHh"
            and not (row.get("AHh") or "").strip()
            and (row.get("B365AH") or "").strip()
        ):
            line_column = "B365AH"
        line_value = _decimal(row.get(line_column), odds=False) if line_column else None
        if line_column and line_value is None:
            invalid += 1
            continue
        quality_flags: tuple[str, ...] = ()
        if season_code == "2526" and spec.bookmaker == "Pinnacle":
            quality_flags = ("provider_warned_stale_after_2025-07-23",)
        source_columns = {
            column: row.get(column, "")
            for column in ((*spec.columns.values(), line_column) if line_column else spec.columns.values())
            if column
        }
        quotes.append(
            HistoricalOddsQuote(
                bookmaker=spec.bookmaker,
                market_type=spec.market_type,
                is_closing=spec.is_closing,
                price_timing=(
                    "closing"
                    if spec.is_closing
                    else "opening"
                    if start_year >= 2019
                    else "preclosing_legacy"
                ),
                # Football-Data CSV files do not provide a per-row capture
                # timestamp for any price column. These values are valid market
                # labels/benchmarks, but not safe as arbitrary earlier as-of features.
                point_in_time_safe=False,
                line_value=line_value,
                odd_home=parsed.get("odd_home"),
                odd_draw=parsed.get("odd_draw"),
                odd_away=parsed.get("odd_away"),
                odd_over=parsed.get("odd_over"),
                odd_under=parsed.get("odd_under"),
                source_columns=source_columns,
                quality_flags=quality_flags,
            )
        )
    return tuple(quotes), invalid


def _invalid_odds_cells(row: dict[str, str]) -> list[dict[str, str]]:
    """Find invalid cells across all bookmaker columns, including unsupported books.

    Historical schemas before Bet365 existed start with other bookmaker prefixes,
    so position-based detection would miss their invalid cells. Handicap lines
    and Betbrain bookmaker-count fields are not prices and are excluded.
    """

    line_or_count_columns = {
        "AHh", "AHCh", "BbAHh", "GBAH", "LBAH", "B365AH",
        "Bb1X2", "BbOU", "BbAH",
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
        raw = (row.get(column) or "").strip()
        if not raw:
            continue
        try:
            parsed = Decimal(raw)
        except InvalidOperation:
            continue
        if parsed.is_finite() and parsed <= 1:
            issues.append({"column": column, "value": raw, "reason": "decimal_odds_must_be_above_1"})
    return issues
