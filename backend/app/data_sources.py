from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from .localization import is_supported_competition, localize_competition, localize_country, localize_round, localize_team


load_dotenv(Path(__file__).resolve().parents[1] / ".env")

API_FOOTBALL_URL = "https://v3.football.api-sports.io"
FOOTBALL_DATA_URL = "https://api.football-data.org/v4"


class ProviderError(RuntimeError):
    pass


def configured_providers() -> dict[str, bool]:
    return {
        "api-football": bool(os.getenv("API_FOOTBALL_API_KEY")),
        "football-data.org": bool(os.getenv("FOOTBALL_DATA_API_KEY")),
    }


def _status(value: str) -> str:
    normalized = value.upper()
    if normalized in {"1H", "HT", "2H", "ET", "BT", "P", "LIVE", "IN_PLAY", "PAUSED"}:
        return "live"
    if normalized in {"FT", "AET", "PEN", "AWD", "FINISHED"}:
        return "finished"
    if normalized in {"PST", "POSTPONED", "SUSP"}:
        return "postponed"
    if normalized in {"CANC", "ABD", "CANCELLED"}:
        return "cancelled"
    return "scheduled"


def normalize_api_football(item: dict[str, Any]) -> dict[str, Any]:
    fixture = item["fixture"]
    league = item["league"]
    teams = item["teams"]
    goals = item.get("goals") or {}
    venue = fixture.get("venue") or {}
    country = league.get("country")
    competition = league.get("name") or "Unknown competition"
    home_name = teams["home"]["name"]
    away_name = teams["away"]["name"]
    round_name = league.get("round")
    return {
        "fixture_id": f"af-{fixture['id']}",
        "provider_ids": {"api-football": str(fixture["id"])},
        "source": "api-football",
        "competition": localize_competition(country, competition),
        "competition_original": competition,
        "competition_code": str(league.get("id") or ""),
        "competition_logo": league.get("logo"),
        "country": localize_country(country),
        "country_original": country,
        "round": localize_round(round_name),
        "round_original": round_name,
        "season_label": str(league.get("season") or "unknown"),
        "season_start": None,
        "season_end": None,
        "kickoff_utc": fixture["date"],
        "status": _status((fixture.get("status") or {}).get("short", "")),
        "status_detail": (fixture.get("status") or {}).get("long"),
        "elapsed": (fixture.get("status") or {}).get("elapsed"),
        "venue": venue.get("name"),
        "city": venue.get("city"),
        "home_team": {
            "id": str(teams["home"].get("id") or ""),
            "name": localize_team(home_name),
            "name_original": home_name,
            "short_name": localize_team(home_name),
            "crest": teams["home"].get("logo"),
        },
        "away_team": {
            "id": str(teams["away"].get("id") or ""),
            "name": localize_team(away_name),
            "name_original": away_name,
            "short_name": localize_team(away_name),
            "crest": teams["away"].get("logo"),
        },
        "score": {"home": goals.get("home"), "away": goals.get("away")},
        "regulation_score": (item.get("score") or {}).get("fulltime") or {},
        "result_duration": "REGULAR" if (fixture.get("status") or {}).get("short") == "FT" else (fixture.get("status") or {}).get("short"),
    }


def football_data_area(item: dict[str, Any]) -> str | None:
    return (item.get("area") or (item.get("competition") or {}).get("area") or {}).get("name")


def supported_football_data(item: dict[str, Any]) -> bool:
    return is_supported_competition(football_data_area(item), (item.get("competition") or {}).get("name", ""))


def normalize_football_data(item: dict[str, Any]) -> dict[str, Any]:
    competition = item.get("competition") or {}
    home = item["homeTeam"]
    away = item["awayTeam"]
    full_time = (item.get("score") or {}).get("fullTime") or {}
    country = football_data_area(item)
    competition_name = competition.get("name") or "Unknown competition"
    home_name = home.get("name") or home.get("shortName")
    away_name = away.get("name") or away.get("shortName")
    round_name = f"Matchday {item['matchday']}" if item.get("matchday") else None
    season = item.get("season") or {}
    return {
        "fixture_id": f"fd-{item['id']}",
        "provider_ids": {"football-data.org": str(item["id"])},
        "source": "football-data.org",
        "competition": localize_competition(country, competition_name),
        "competition_original": competition_name,
        "competition_code": competition.get("code") or str(competition.get("id") or ""),
        "competition_logo": competition.get("emblem"),
        "country": localize_country(country),
        "country_original": country,
        "round": localize_round(round_name),
        "round_original": round_name,
        "season_label": (
            str(season["startDate"])[:4] + "/" + str(season["endDate"])[2:4]
            if season.get("startDate") and season.get("endDate")
            and str(season["startDate"])[:4] != str(season["endDate"])[:4]
            else str(season.get("startDate", "unknown"))[:4]
        ),
        "season_start": season.get("startDate"),
        "season_end": season.get("endDate"),
        "kickoff_utc": item["utcDate"],
        "status": _status(item.get("status", "")),
        "status_detail": item.get("status", "").replace("_", " ").title(),
        "elapsed": None,
        "venue": item.get("venue"),
        "city": None,
        "home_team": {
            "id": str(home.get("id") or ""),
            "name": localize_team(home_name),
            "name_original": home_name,
            "short_name": localize_team(home.get("shortName") or home_name),
            "crest": home.get("crest"),
        },
        "away_team": {
            "id": str(away.get("id") or ""),
            "name": localize_team(away_name),
            "name_original": away_name,
            "short_name": localize_team(away.get("shortName") or away_name),
            "crest": away.get("crest"),
        },
        "score": {"home": full_time.get("home"), "away": full_time.get("away")},
        "regulation_score": full_time if (item.get("score") or {}).get("duration") == "REGULAR" else (item.get("score") or {}).get("regularTime") or {},
        "result_duration": (item.get("score") or {}).get("duration"),
    }


async def fetch_api_football(client: httpx.AsyncClient, date_from: date, date_to: date) -> list[dict[str, Any]]:
    token = os.getenv("API_FOOTBALL_API_KEY")
    if not token:
        raise ProviderError("API key not configured")
    # The free plan accepts a single date but not an unrestricted date range.
    # Seven daily calls stay below the 10 calls/minute limit and are cached.
    request_days = min((date_to - date_from).days + 1, 7)
    normalized: list[dict[str, Any]] = []
    for offset in range(request_days):
        requested_date = date_from + timedelta(days=offset)
        response = await client.get(
            f"{API_FOOTBALL_URL}/fixtures",
            params={"date": requested_date.isoformat(), "timezone": "UTC"},
            headers={"x-apisports-key": token},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            # API-Football's free plan currently exposes the present day but
            # rejects future dates. Keep the valid days already collected and
            # let football-data.org cover future fixtures where available.
            if offset > 0:
                break
            raise ProviderError(str(payload["errors"]))
        normalized.extend(
            normalize_api_football(item)
            for item in payload.get("response", [])
            if is_supported_competition(item["league"].get("country"), item["league"].get("name", ""))
        )
    return normalized


async def fetch_football_data(client: httpx.AsyncClient, date_from: date, date_to: date) -> list[dict[str, Any]]:
    token = os.getenv("FOOTBALL_DATA_API_KEY")
    if not token:
        raise ProviderError("API key not configured")
    response = await client.get(
        f"{FOOTBALL_DATA_URL}/matches",
        params={"dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat()},
        headers={"X-Auth-Token": token},
    )
    response.raise_for_status()
    return [
        normalize_football_data(item)
        for item in response.json().get("matches", [])
        if supported_football_data(item)
    ]


def _team_key(name: str) -> str:
    return re.sub(r"\b(fc|cf|afc|club|calcio)\b|[^a-z0-9]", "", name.lower())


def _dedupe_key(item: dict[str, Any]) -> tuple[str, str, str]:
    kickoff = datetime.fromisoformat(item["kickoff_utc"].replace("Z", "+00:00")).strftime("%Y%m%d%H%M")
    return (
        kickoff,
        _team_key(item["home_team"].get("name_original") or item["home_team"]["name"]),
        _team_key(item["away_team"].get("name_original") or item["away_team"]["name"]),
    )


def merge_fixtures(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in items:
        key = _dedupe_key(item)
        if key in merged:
            current = merged[key]
            current["provider_ids"].update(item["provider_ids"])
            current["source"] = " + ".join(sorted(current["provider_ids"]))
            for field in ("venue", "city", "round", "competition_logo"):
                current[field] = current.get(field) or item.get(field)
        else:
            merged[key] = item
    return sorted(merged.values(), key=lambda item: item["kickoff_utc"])


class FixtureService:
    def __init__(self) -> None:
        self._cache: dict[tuple[date, date], tuple[float, dict[str, Any]]] = {}

    async def fetch(self, date_from: date, date_to: date) -> dict[str, Any]:
        cache_key = (date_from, date_to)
        cached = self._cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            return {**cached[1], "cached": True}

        providers: dict[str, dict[str, Any]] = {}
        all_items: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=15) as client:
            results = await asyncio.gather(
                fetch_api_football(client, date_from, date_to),
                fetch_football_data(client, date_from, date_to),
                return_exceptions=True,
            )

        for name, result in zip(("api-football", "football-data.org"), results, strict=True):
            if isinstance(result, Exception):
                providers[name] = {"status": "error", "count": 0, "message": _safe_error(result)}
            else:
                providers[name] = {
                    "status": "ok",
                    "count": len(result),
                    "coverage": (
                        date_from.isoformat()
                        if name == "api-football"
                        else f"{date_from.isoformat()}..{date_to.isoformat()}"
                    ),
                }
                all_items.extend(result)

        if all(provider["status"] == "error" for provider in providers.values()):
            raise ProviderError("All configured fixture providers are unavailable")

        payload = {
            "date_from": date_from,
            "date_to": date_to,
            "fetched_at_utc": datetime.now(UTC).isoformat(),
            "cached": False,
            "providers": providers,
            "items": merge_fixtures(all_items),
        }
        ttl = max(30, int(os.getenv("FIXTURE_CACHE_SECONDS", "300")))
        self._cache[cache_key] = (time.monotonic() + ttl, payload)
        return payload


def _safe_error(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    if isinstance(error, (httpx.RequestError, ProviderError)):
        return str(error)[:180]
    return "Provider request failed"


fixture_service = FixtureService()
