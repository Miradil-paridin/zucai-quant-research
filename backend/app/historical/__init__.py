"""Auditable historical football data ingestion."""

from .football_data_couk import (
    BIG_FIVE_LEAGUES,
    DEFAULT_SEASONS,
    FootballDataCoUkAdapter,
    HistoricalMatchRecord,
    HistoricalOddsQuote,
    LeagueDefinition,
    ParsedHistoricalFile,
    SeasonDefinition,
    SourceFileSpec,
    all_verified_seasons,
    season_from_code,
)
from .football_data_nordic import (
    NORDIC_LEAGUES,
    NORDIC_SPECS,
    FootballDataNordicAdapter,
)

__all__ = [
    "BIG_FIVE_LEAGUES",
    "DEFAULT_SEASONS",
    "FootballDataCoUkAdapter",
    "HistoricalMatchRecord",
    "HistoricalOddsQuote",
    "LeagueDefinition",
    "ParsedHistoricalFile",
    "SeasonDefinition",
    "SourceFileSpec",
    "all_verified_seasons",
    "season_from_code",
    "NORDIC_LEAGUES",
    "NORDIC_SPECS",
    "FootballDataNordicAdapter",
]
