from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .collector import CollectorBusyError, collector
from .database import database_health, initialize_database
from .data_sources import configured_providers
from .demo_data import BACKTEST, MATCHES, OPS
from .historical.repository import historical_repository
from .repository import repository
from .research.service import (
    historical_matches_response,
    latest_backtest_response,
    run_default_backtest,
    teams_response,
)
from .scheduler import scheduler_status, start_scheduler, stop_scheduler
from .schemas import MatchSummary, WatchlistRequest, WatchlistResponse


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="Football Quant Research API",
    version="0.1.0",
    description="Auditable football research, probability and simulation API.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:5174", "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, object]:
    providers = configured_providers()
    database = database_health()
    return {
        "status": "ok",
        "service": "football-quant-api",
        "version": app.version,
        "time_utc": datetime.now(UTC),
        "mode": "live" if any(providers.values()) else "demo",
        "providers": providers,
        "database": database,
    }


@app.get("/api/ingestion/status")
def ingestion_status() -> dict[str, object]:
    return {"scheduler": scheduler_status(), "storage": repository.stats()}


@app.get("/api/historical/status")
def historical_status() -> dict[str, object]:
    return {
        "source": "football-data.co.uk",
        "storage": "postgresql",
        "provenance": "raw CSV + SHA-256 + source URL + parser validation report",
        **historical_repository.stats(),
    }


@app.get("/api/historical/matches")
def historical_matches(
    competition: str | None = Query(default=None),
    season: str | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    try:
        return historical_matches_response(
            competition_code=competition,
            season_label=season,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/research/teams")
def research_teams(
    competition: str = Query(default="PL"),
    as_of: date | None = Query(default=None),
    window: int = Query(default=20, ge=5, le=50),
) -> dict[str, object]:
    try:
        return teams_response(
            competition_code=competition,
            as_of=as_of,
            window=window,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/api/research/backtests/latest")
def research_backtest_latest() -> dict[str, object]:
    return latest_backtest_response()


@app.post("/api/research/backtests/run")
def research_backtest_run() -> dict[str, object]:
    return run_default_backtest()


@app.post("/api/ingestion/run")
async def run_ingestion(days: int = Query(default=7, ge=1, le=7)) -> dict[str, object]:
    try:
        return await collector.run(days=days)
    except CollectorBusyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/fixtures")
async def list_fixtures(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    days: int = Query(default=7, ge=1, le=7),
    competition: str | None = Query(default=None),
) -> dict[str, object]:
    start = date_from or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    end = date_to or (start + timedelta(days=days - 1))
    if end < start:
        raise HTTPException(status_code=422, detail="date_to must not be before date_from")
    if (end - start).days > 6:
        raise HTTPException(status_code=422, detail="date range cannot exceed 7 days")
    stored_items = repository.list_fixtures(start, end, competition)
    provider_configuration = configured_providers()
    api_football_count = sum(
        "api-football" in item["provider_ids"] for item in stored_items
    )
    football_data_count = sum(
        "football-data.org" in item["provider_ids"] for item in stored_items
    )
    if stored_items:
        return {
            "date_from": start,
            "date_to": end,
            "fetched_at_utc": max(
                (item.get("last_source_update_utc") for item in stored_items if item.get("last_source_update_utc")),
                default=datetime.now(UTC),
            ),
            "cached": True,
            "storage": "postgresql",
            "providers": {
                "api-football": {
                    "status": "ok" if api_football_count or provider_configuration["api-football"] else "not_configured",
                    "count": api_football_count,
                    "coverage": start.isoformat(),
                },
                "football-data.org": {
                    "status": "ok" if football_data_count or provider_configuration["football-data.org"] else "not_configured",
                    "count": football_data_count,
                    "coverage": f"{start.isoformat()}..{end.isoformat()}",
                },
            },
            "items": stored_items,
        }
    return {
        "date_from": start,
        "date_to": end,
        "fetched_at_utc": datetime.now(UTC),
        "cached": True,
        "storage": "postgresql",
        "providers": {
            "api-football": {
                "status": "ok" if provider_configuration["api-football"] else "not_configured",
                "count": 0,
                "coverage": start.isoformat(),
            },
            "football-data.org": {
                "status": "ok" if provider_configuration["football-data.org"] else "not_configured",
                "count": 0,
                "coverage": f"{start.isoformat()}..{end.isoformat()}",
            },
        },
        "items": [],
    }


@app.get("/api/dashboard/summary")
def dashboard_summary() -> dict[str, object]:
    return {
        "data_status": "demo_not_for_decision",
        "date": "2026-07-20",
        "timezone": "Asia/Shanghai",
        "phase": "simulation",
        "match_count": len(MATCHES),
        "data_completeness": OPS["core_completeness"],
        "model_version": BACKTEST["model_version"],
    }


@app.get("/api/matches", response_model=list[MatchSummary])
def list_matches(competition: str | None = Query(default=None)) -> list[dict[str, object]]:
    if competition is None:
        return MATCHES
    return [match for match in MATCHES if match["competition"] == competition]


@app.get("/api/matches/{match_id}", response_model=MatchSummary)
def get_match(match_id: str) -> dict[str, object]:
    match = next((item for item in MATCHES if item["match_id"] == match_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="match not found")
    return match


@app.get("/api/backtests/summary")
def backtest_summary() -> dict[str, object]:
    return {"data_status": "demo_not_for_decision", **BACKTEST}


@app.get("/api/ops/health")
def ops_health() -> dict[str, object]:
    return {"data_status": "demo_not_for_decision", **OPS}


@app.post("/api/simulations/watchlist", response_model=WatchlistResponse, status_code=201)
def add_to_watchlist(request: WatchlistRequest) -> WatchlistResponse:
    match = next((item for item in MATCHES if item["match_id"] == request.match_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="match not found")
    if match["lineup_confidence"] < 0.70:
        raise HTTPException(status_code=409, detail="lineup confidence below hard safety floor")
    return WatchlistResponse(
        audit_id=str(uuid4()),
        match_id=request.match_id,
        created_at_utc=datetime.now(UTC),
        message="Added to the simulation watchlist; no real-money action was created.",
    )
