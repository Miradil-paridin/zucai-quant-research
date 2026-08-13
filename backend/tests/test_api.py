import asyncio
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

import app.collector as collector_module
import app.main as main_module
from app.main import app
from app.collector import IngestionCollector
from app.data_sources import fetch_api_football, normalize_api_football, normalize_football_data
from app.repository import repository
from app.localization import is_supported_competition, localize_competition, localize_country, localize_round, localize_team


client = TestClient(app)


def test_health(monkeypatch) -> None:
    monkeypatch.delenv("API_FOOTBALL_API_KEY", raising=False)
    monkeypatch.delenv("FOOTBALL_DATA_API_KEY", raising=False)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["mode"] == "demo"


def test_ingestion_status_degrades_cleanly_when_database_is_down(monkeypatch) -> None:
    monkeypatch.setattr(
        main_module,
        "database_health",
        lambda: {"status": "error", "message": "connection timeout"},
    )
    monkeypatch.setattr(
        repository,
        "stats",
        lambda: (_ for _ in ()).throw(AssertionError("stats must not run while DB is down")),
    )

    response = client.get("/api/ingestion/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["database"]["status"] == "error"
    assert payload["storage"] == {"available": False, "counts": {}, "latest_batches": []}


def test_ingestion_status_does_not_misdiagnose_a_stats_bug_as_db_down(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "database_health", lambda: {"status": "ok"})
    monkeypatch.setattr(
        repository,
        "stats",
        lambda: (_ for _ in ()).throw(KeyError("unexpected result shape")),
    )

    payload = client.get("/api/ingestion/status").json()

    assert payload["database"]["status"] == "ok"
    assert payload["storage"]["available"] is False
    assert "KeyError" in payload["storage"]["error"]


def test_ingestion_fails_before_provider_calls_when_database_is_down(monkeypatch) -> None:
    provider_called = False

    def fail_connect():
        raise RuntimeError("database unavailable")

    async def unexpected_provider_call(*_args, **_kwargs):
        nonlocal provider_called
        provider_called = True
        return {}

    collector = IngestionCollector()
    monkeypatch.setattr(collector_module, "connect", fail_connect)
    monkeypatch.setattr(collector, "_api_football_quota", unexpected_provider_call)

    with pytest.raises(RuntimeError, match="database unavailable"):
        asyncio.run(collector._run(days=1))
    assert provider_called is False


def test_match_probabilities_are_valid() -> None:
    response = client.get("/api/matches/m-1042")
    assert response.status_code == 200
    assert response.json()["data_status"] == "demo_not_for_decision"
    probabilities = response.json()["model_probability"]
    assert abs(sum(probabilities.values()) - 1) < 0.0001


def test_demo_research_endpoints_cannot_masquerade_as_computed_results() -> None:
    assert client.get("/api/dashboard/summary").json()["data_status"] == "demo_not_for_decision"
    assert client.get("/api/backtests/summary").json()["data_status"] == "demo_not_for_decision"
    assert client.get("/api/ops/health").json()["data_status"] == "demo_not_for_decision"


def test_watchlist_is_simulation_only() -> None:
    response = client.post("/api/simulations/watchlist", json={"match_id": "m-1042"})
    assert response.status_code == 201
    assert response.json()["status"] == "observing"


def test_api_football_normalization() -> None:
    fixture = normalize_api_football({
        "fixture": {
            "id": 42,
            "date": "2026-07-21T12:00:00+00:00",
            "status": {"short": "NS", "long": "Not Started", "elapsed": None},
            "venue": {"name": "Test Ground", "city": "London"},
        },
        "league": {"id": 39, "name": "Premier League", "country": "England", "logo": None, "round": "Regular Season - 1"},
        "teams": {
            "home": {"id": 1, "name": "Home FC", "logo": None},
            "away": {"id": 2, "name": "Away FC", "logo": None},
        },
        "goals": {"home": None, "away": None},
    })
    assert fixture["fixture_id"] == "af-42"
    assert fixture["status"] == "scheduled"
    assert fixture["home_team"]["name"] == "Home FC"


def test_football_data_normalization() -> None:
    fixture = normalize_football_data({
        "id": 84,
        "utcDate": "2026-07-22T18:00:00Z",
        "status": "TIMED",
        "matchday": 2,
        "competition": {"id": 2021, "name": "Premier League", "code": "PL", "emblem": None, "area": {"name": "England"}},
        "homeTeam": {"id": 3, "name": "North FC", "shortName": "North", "crest": None},
        "awayTeam": {"id": 4, "name": "South FC", "shortName": "South", "crest": None},
        "score": {"fullTime": {"home": None, "away": None}},
    })
    assert fixture["fixture_id"] == "fd-84"
    assert fixture["competition_code"] == "PL"
    assert fixture["round"] == "第2轮"


def test_fixture_feed_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(repository, "list_fixtures", lambda *_args: [{
        "competition": "Premier League",
        "provider_ids": {"api-football": "42"},
        "last_source_update_utc": None,
    }])
    response = client.get("/api/fixtures?date_from=2026-07-20&days=1")
    assert response.status_code == 200
    assert response.json()["items"][0]["competition"] == "Premier League"


def test_chinese_localization_preserves_known_football_names() -> None:
    assert localize_country("England") == "英格兰"
    assert localize_competition("England", "Premier League") == "英格兰超级联赛"
    assert localize_team("Arsenal") == "阿森纳"
    assert localize_team("Nott'm Forest") == "诺丁汉森林"
    assert localize_team("Bodo/Glimt") == "博德闪耀"
    assert localize_round("Regular Season - 8") == "常规赛 · 第8轮"
    assert localize_team("Unmapped Town") == "Unmapped Town"
    assert is_supported_competition("England", "Premier League") is True
    assert is_supported_competition("Lebanon", "Premier League") is False


def test_api_football_keeps_today_when_free_plan_rejects_future(monkeypatch) -> None:
    monkeypatch.setenv("API_FOOTBALL_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["date"] == "2026-07-20":
            return httpx.Response(200, json={"errors": [], "response": [{
                "fixture": {"id": 1, "date": "2026-07-20T18:00:00Z", "status": {"short": "NS", "long": "Not Started"}, "venue": {}},
                "league": {"id": 113, "name": "Allsvenskan", "country": "Sweden", "logo": None, "round": "Regular Season - 16"},
                "teams": {"home": {"id": 1, "name": "Malmo FF", "logo": None}, "away": {"id": 2, "name": "Kalmar FF", "logo": None}},
                "goals": {"home": None, "away": None},
            }]})
        return httpx.Response(200, json={"errors": {"plan": "future date unavailable"}, "response": []})

    async def run() -> list[dict]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_api_football(client, date(2026, 7, 20), date(2026, 7, 26))

    result = asyncio.run(run())
    assert len(result) == 1
    assert result[0]["competition"] == "瑞典超级联赛"


def test_api_football_quota_probe_fails_closed() -> None:
    async def run() -> dict:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "temporarily unavailable"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await IngestionCollector()._api_football_quota(client, "test-key")

    quota = asyncio.run(run())
    assert quota["status"] == "unavailable"
    assert quota["remaining"] == 0
