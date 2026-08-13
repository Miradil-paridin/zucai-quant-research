from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.historical.football_data_couk import (
    BIG_FIVE_LEAGUES,
    DownloadedSourceFile,
    FootballDataCoUkAdapter,
    HistoricalDataValidationError,
    SeasonDefinition,
    SourceFileSpec,
    all_verified_seasons,
    season_from_code,
)
from app.historical.football_data_nordic import (
    NORDIC_SPECS,
    FootballDataNordicAdapter,
)


HEADER = (
    "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,"
    "B365H,B365D,B365A,PSH,PSD,PSA,B365CH,B365CD,B365CA,"
    "B365>2.5,B365<2.5,B365C>2.5,B365C<2.5,AHh,B365AHH,B365AHA,"
    "AHCh,B365CAHH,B365CAHA"
)


def downloaded_file(tmp_path: Path, rows: list[str], season_code: str = "2425") -> DownloadedSourceFile:
    path = tmp_path / "E0.csv"
    content = (HEADER + "\n" + "\n".join(rows) + "\n").encode()
    path.write_bytes(content)
    season = SeasonDefinition(season_code, "2025/2026" if season_code == "2526" else "2024/2025")
    return DownloadedSourceFile(
        spec=SourceFileSpec(BIG_FIVE_LEAGUES[0], season),
        path=path,
        content_sha256=hashlib.sha256(content).hexdigest(),
        content_bytes=len(content),
        http_etag='"test"',
        http_last_modified="Mon, 01 Jan 2025 00:00:00 GMT",
    )


def valid_row() -> str:
    return (
        "E0,16/08/2024,20:00,Manchester United,Fulham,1,0,H,0,0,D,"
        "1.60,4.20,5.50,1.62,4.10,5.40,1.58,4.25,5.70,"
        "1.90,1.95,1.87,2.01,-0.75,1.95,1.90,-1.0,2.05,1.80"
    )


def test_real_csv_shape_parses_results_and_odds(tmp_path: Path) -> None:
    parsed = FootballDataCoUkAdapter(tmp_path).parse(downloaded_file(tmp_path, [valid_row()]))

    assert len(parsed.records) == 1
    record = parsed.records[0]
    assert record.home_team == "Manchester United"
    assert record.home_team_norm == "manchesterunited"
    assert record.full_time_result == "H"
    assert len(record.odds) == 7
    assert {quote.price_timing for quote in record.odds} == {"opening", "closing"}
    assert not any(quote.point_in_time_safe for quote in record.odds)
    assert parsed.validation_report["sha256"] == parsed.downloaded.content_sha256
    assert parsed.validation_report["rejected_count"] == 0


def test_continental_csv_time_uses_verified_source_timezone() -> None:
    assert {league.timezone for league in BIG_FIVE_LEAGUES} == {"Europe/London"}


def test_verified_archive_season_codes_are_deterministic() -> None:
    seasons = all_verified_seasons()

    assert len(seasons) == 33
    assert seasons[0] == SeasonDefinition("9394", "1993/1994")
    assert seasons[-1] == SeasonDefinition("2526", "2025/2026")
    assert season_from_code("9900") == SeasonDefinition("9900", "1999/2000")
    assert season_from_code("0001") == SeasonDefinition("0001", "2000/2001")


@pytest.mark.parametrize("code", ["9293", "2527", "abcd", "242"])
def test_unverified_or_malformed_season_codes_are_refused(code: str) -> None:
    with pytest.raises(ValueError):
        season_from_code(code)


def test_score_result_conflict_is_rejected_without_fabrication(tmp_path: Path) -> None:
    bad = valid_row().replace(",1,0,H,0,0,D,", ",1,0,A,0,0,D,")

    with pytest.raises(HistoricalDataValidationError, match="no valid completed matches"):
        FootballDataCoUkAdapter(tmp_path).parse(downloaded_file(tmp_path, [bad]))


def test_bad_row_is_reported_while_valid_row_remains(tmp_path: Path) -> None:
    incomplete = "E0,17/08/2024,15:00,Arsenal,Wolves,,,,,,,,,,,,,,,,,,,,,,,,,"
    parsed = FootballDataCoUkAdapter(tmp_path).parse(
        downloaded_file(tmp_path, [valid_row(), incomplete])
    )

    assert len(parsed.records) == 1
    assert parsed.validation_report["rejected_count"] == 1
    assert parsed.validation_report["rejected_rows"][0]["row"] == 3
    assert "missing required value FTHG" in parsed.validation_report["rejected_rows"][0]["reason"]


def test_pinnacle_2025_26_odds_are_preserved_but_flagged(tmp_path: Path) -> None:
    row = valid_row().replace("16/08/2024", "16/08/2025")
    parsed = FootballDataCoUkAdapter(tmp_path).parse(
        downloaded_file(tmp_path, [row], season_code="2526")
    )

    pinnacle = [quote for quote in parsed.records[0].odds if quote.bookmaker == "Pinnacle"]
    assert pinnacle
    assert all("provider_warned_stale_after_2025-07-23" in quote.quality_flags for quote in pinnacle)
    assert any("Pinnacle" in warning for warning in parsed.validation_report["warnings"])


def test_zero_odds_are_audited_and_not_imported(tmp_path: Path) -> None:
    row = valid_row().replace("1.90,1.95,1.87,2.01", "0,1.95,0,2.01")
    parsed = FootballDataCoUkAdapter(tmp_path).parse(downloaded_file(tmp_path, [row]))

    report = parsed.validation_report
    assert report["invalid_odds_cell_count"] == 2
    assert {item["column"] for item in report["invalid_odds_cells"]} == {
        "B365>2.5",
        "B365C>2.5",
    }
    assert not [
        quote for quote in parsed.records[0].odds
        if quote.bookmaker == "Bet365" and quote.market_type == "OU25"
    ]


def test_pre_bet365_schema_preserves_documented_legacy_bookmakers(tmp_path: Path) -> None:
    content = (
        "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,GBH,GBD,GBA,IWH,IWD,IWA,"
        "LBH,LBD,LBA,WHH,WHD,WHA\n"
        "E0,19/08/2000,Charlton,Man City,4,0,H,2.10,3.20,3.40,2.15,3.10,3.30,"
        "2.05,3.25,3.50,2.00,3.20,3.60\n"
    ).encode()
    path = tmp_path / "E0.csv"
    path.write_bytes(content)
    downloaded = DownloadedSourceFile(
        spec=SourceFileSpec(BIG_FIVE_LEAGUES[0], season_from_code("0001")),
        path=path,
        content_sha256=hashlib.sha256(content).hexdigest(),
        content_bytes=len(content),
        http_etag=None,
        http_last_modified=None,
    )

    parsed = FootballDataCoUkAdapter(tmp_path).parse(downloaded)
    bookmakers = {quote.bookmaker for quote in parsed.records[0].odds}

    assert bookmakers == {"Gamebookers", "Interwetten", "Ladbrokes", "William Hill"}
    assert {quote.price_timing for quote in parsed.records[0].odds} == {"preclosing_legacy"}
    assert parsed.validation_report["invalid_odds_cell_count"] == 0


def test_invalid_legacy_odds_are_audited_without_bet365_column(tmp_path: Path) -> None:
    content = (
        "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,GBH,GBD,GBA\n"
        "E0,19/08/2000,Charlton,Man City,4,0,H,0,3.20,3.40\n"
    ).encode()
    path = tmp_path / "E0.csv"
    path.write_bytes(content)
    downloaded = DownloadedSourceFile(
        spec=SourceFileSpec(BIG_FIVE_LEAGUES[0], season_from_code("0001")),
        path=path,
        content_sha256=hashlib.sha256(content).hexdigest(),
        content_bytes=len(content),
        http_etag=None,
        http_last_modified=None,
    )

    parsed = FootballDataCoUkAdapter(tmp_path).parse(downloaded)

    assert parsed.records[0].odds == ()
    assert parsed.validation_report["invalid_odds_cell_count"] == 1
    assert parsed.validation_report["invalid_odds_cells"][0]["column"] == "GBH"


def nordic_file(tmp_path: Path, rows: list[str]) -> DownloadedSourceFile:
    header = (
        "Country,League,Season,Date,Time,Home,Away,HG,AG,Res,"
        "PSCH,PSCD,PSCA,MaxCH,MaxCD,MaxCA,AvgCH,AvgCD,AvgCA,"
        "BFECH,BFECD,BFECA,B365CH,B365CD,B365CA"
    )
    content = (header + "\n" + "\n".join(rows) + "\n").encode()
    path = tmp_path / "FIN.csv"
    path.write_bytes(content)
    return DownloadedSourceFile(
        spec=NORDIC_SPECS[0],
        path=path,
        content_sha256=hashlib.sha256(content).hexdigest(),
        content_bytes=len(content),
        http_etag=None,
        http_last_modified=None,
        retrieved_from_network=False,
    )


def test_nordic_parser_trims_identity_and_quarantines_warned_pinnacle(tmp_path: Path) -> None:
    rows = [
        "Finland ,Veikkausliiga ,2025,01/07/2025,16:00,HJK,KuPS,1,0,H,"
        "1.8,3.5,4.2,1.9,3.6,4.4,1.75,3.4,4.1,,,,,2.0,3.5,3.8",
        "Finland,Veikkausliiga,2025,01/08/2025,17:00,Ilves,SJK,2,2,D,"
        "2.1,3.4,3.2,2.2,3.5,3.3,2.0,3.3,3.1,,,,,2.15,3.4,3.2",
    ]

    parsed = FootballDataNordicAdapter(tmp_path).parse(nordic_file(tmp_path, rows))

    assert len(parsed.records) == 2
    assert parsed.records[0].source_timezone == "unknown"
    assert "source_time_timezone_unverified" in parsed.records[0].quality_flags
    assert "partial_season_snapshot" in parsed.records[0].quality_flags
    assert any(quote.bookmaker == "Pinnacle" for quote in parsed.records[0].odds)
    assert not any(quote.bookmaker == "Pinnacle" for quote in parsed.records[1].odds)
    assert parsed.validation_report["pinnacle_quote_groups_quarantined"] == 1
    assert parsed.validation_report["rejected_count"] == 0


def test_nordic_market_average_above_max_is_quarantined_not_repaired(tmp_path: Path) -> None:
    row = (
        "Finland,Veikkausliiga,2021,31/08/2021,16:30,KTP,Inter Turku,1,3,A,"
        "4.5,3.8,1.7,4.6,3.9,1.29,4.4,3.7,1.75,,,,,,,"
    )

    parsed = FootballDataNordicAdapter(tmp_path).parse(nordic_file(tmp_path, [row]))
    bookmakers = {quote.bookmaker for quote in parsed.records[0].odds}

    assert "MarketMax" not in bookmakers
    assert "MarketAverage" not in bookmakers
    assert "Pinnacle" in bookmakers
    assert parsed.validation_report["market_consistency_quote_groups_quarantined"] == 2
    assert parsed.validation_report["anomaly_samples"][0]["reason"] == (
        "market_average_exceeds_market_maximum"
    )


def test_html_response_is_refused() -> None:
    spec = SourceFileSpec(BIG_FIVE_LEAGUES[0], SeasonDefinition("2425", "2024/2025"))

    with pytest.raises(HistoricalDataValidationError, match="missing required columns"):
        FootballDataCoUkAdapter._validate_download(spec, b"<html>not csv</html>" * 20, "text/html")
