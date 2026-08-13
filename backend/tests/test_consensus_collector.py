from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from psycopg import OperationalError, ProgrammingError

from app.jingcai import consensus


def stub_batch_io(monkeypatch, *, reserve="reserved"):
    finished = []
    monkeypatch.setattr(consensus, "validate_provider_configuration", lambda: None)
    monkeypatch.setattr(consensus, "_start_batch", lambda *_args: 42)
    monkeypatch.setattr(consensus, "_reserve_request", lambda *_args: reserve)
    monkeypatch.setattr(consensus, "_checkpoint_batch", lambda *_args: None)
    monkeypatch.setattr(consensus, "_finish_batch", lambda _batch_id, summary: finished.append(dict(summary)))
    return finished


def test_empty_provider_response_is_a_successful_collection(monkeypatch) -> None:
    finished = stub_batch_io(monkeypatch)
    monkeypatch.setattr(consensus, "fetch_sport_odds", lambda _key: ([], "250"))
    monkeypatch.setattr(consensus, "_store_sport_events", lambda *_args: (0, 0))

    result = consensus.collect_consensus_once(
        trigger="daily_heartbeat",
        sport_keys=["soccer_epl"],
        provider_quota_remaining=251,
    )

    assert result["status"] == "succeeded"
    assert result["sports_succeeded"] == 1
    assert result["events_seen"] == 0
    assert result["quota_remaining"] == 250
    assert finished[0]["status"] == "succeeded"


def test_one_sport_failure_is_reported_as_partial(monkeypatch) -> None:
    finished = stub_batch_io(monkeypatch)

    def fetch(key):
        if key == "soccer_spain_la_liga":
            raise RuntimeError("HTTP 503")
        return ([{"id": "event"}], "249")

    monkeypatch.setattr(consensus, "fetch_sport_odds", fetch)
    monkeypatch.setattr(consensus, "_store_sport_events", lambda *_args: (2, 3))

    result = consensus.collect_consensus_once(
        trigger="daily_heartbeat",
        sport_keys=["soccer_epl", "soccer_spain_la_liga"],
        provider_quota_remaining=251,
    )

    assert result["status"] == "partial"
    assert result["sports_attempted"] == 2
    assert result["sports_succeeded"] == 1
    assert result["snapshots_inserted"] == 2
    assert len(result["errors"]) == 1
    assert finished[0]["status"] == "partial"


def test_local_budget_stops_before_external_request(monkeypatch) -> None:
    finished = stub_batch_io(monkeypatch, reserve="monthly_budget_exhausted")
    external_calls = []
    monkeypatch.setattr(consensus, "fetch_sport_odds", lambda key: external_calls.append(key))

    result = consensus.collect_consensus_once(
        trigger="daily_heartbeat",
        sport_keys=["soccer_epl"],
        provider_quota_remaining=250,
    )

    assert external_calls == []
    assert result["status"] == "failed"
    assert result["stop_reason"] == "monthly_budget_exhausted"
    assert finished[0]["sports_attempted"] == 0


def test_provider_reserve_stops_before_reserving_local_request(monkeypatch) -> None:
    finished = []
    reservations = []
    monkeypatch.setattr(consensus, "validate_provider_configuration", lambda: None)
    monkeypatch.setattr(consensus, "_start_batch", lambda *_args: 42)
    monkeypatch.setattr(consensus, "_reserve_request", lambda *_args: reservations.append("reserved"))
    monkeypatch.setattr(consensus, "_checkpoint_batch", lambda *_args: None)
    monkeypatch.setattr(consensus, "_finish_batch", lambda _batch_id, summary: finished.append(dict(summary)))

    result = consensus.collect_consensus_once(
        trigger="daily_heartbeat",
        sport_keys=["soccer_epl"],
        provider_quota_remaining=100,
    )

    assert reservations == []
    assert result["stop_reason"] == "provider_quota_reserve"
    assert result["status"] == "failed"


def test_rate_limit_response_updates_quota_and_stops(monkeypatch) -> None:
    finished = stub_batch_io(monkeypatch)
    calls = []

    def fetch(key):
        calls.append(key)
        raise consensus.ProviderRequestError(key, 429, "rate limited", None)

    monkeypatch.setattr(consensus, "fetch_sport_odds", fetch)

    result = consensus.collect_consensus_once(
        trigger="daily_heartbeat",
        sport_keys=["soccer_epl", "soccer_spain_la_liga"],
        provider_quota_remaining=250,
    )

    assert calls == ["soccer_epl"]
    assert result["status"] == "failed"
    assert result["stop_reason"] == "provider_rejected_requests"
    assert result["quota_remaining"] == 0
    assert finished[0]["quota_remaining"] == 0


def test_request_failure_without_quota_header_invalidates_old_quota(monkeypatch) -> None:
    finished = stub_batch_io(monkeypatch)
    calls = []

    def fetch(key):
        calls.append(key)
        raise TimeoutError("network timeout")

    monkeypatch.setattr(consensus, "fetch_sport_odds", fetch)

    result = consensus.collect_consensus_once(
        trigger="daily_heartbeat",
        sport_keys=["soccer_epl", "soccer_spain_la_liga"],
        provider_quota_remaining=101,
    )

    assert calls == ["soccer_epl"]
    assert result["quota_remaining"] == 101
    assert result["quota_verified"] is False
    assert finished[0]["quota_verified"] is False


def test_invalid_json_retains_quota_header(monkeypatch) -> None:
    class Response:
        status_code = 200
        headers = {"x-requests-remaining": "100"}
        text = "not-json"

        @staticmethod
        def json():
            raise ValueError("invalid json")

    fake_requests = SimpleNamespace(get=lambda *_args, **_kwargs: Response())
    monkeypatch.setitem(sys.modules, "curl_cffi", SimpleNamespace(requests=fake_requests))
    monkeypatch.setenv("ODDS_API_KEY", "test-key")

    with pytest.raises(consensus.ProviderRequestError) as caught:
        consensus.fetch_sport_odds("soccer_epl")
    assert caught.value.status_code == 200
    assert caught.value.quota_remaining == "100"


def test_invalid_quota_values_fail_closed() -> None:
    assert consensus._quota_int("-1") is None
    assert consensus._quota_int("1000000000") is None
    assert consensus._quota_int("not-a-number") is None


def test_missing_quota_header_stops_after_one_request(monkeypatch) -> None:
    finished = stub_batch_io(monkeypatch)
    calls = []
    monkeypatch.setattr(
        consensus,
        "fetch_sport_odds",
        lambda key: (calls.append(key) or [], None),
    )
    monkeypatch.setattr(consensus, "_store_sport_events", lambda *_args: (0, 0))

    result = consensus.collect_consensus_once(
        trigger="daily_heartbeat",
        sport_keys=["soccer_epl", "soccer_spain_la_liga"],
        provider_quota_remaining=250,
    )

    assert calls == ["soccer_epl"]
    assert result["status"] == "partial"
    assert result["stop_reason"] == "provider_quota_unknown"
    assert finished[0]["sports_succeeded"] == 1


def test_missing_api_key_does_not_reserve_budget(monkeypatch) -> None:
    reservations = []
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setattr(consensus, "_reserve_request", lambda *_args: reservations.append("reserved"))

    with pytest.raises(RuntimeError, match="ODDS_API_KEY"):
        consensus.collect_consensus_once(
            trigger="daily_heartbeat",
            sport_keys=["soccer_epl"],
            provider_quota_remaining=250,
        )
    assert reservations == []


def test_fatal_database_error_attempts_to_finalize_batch(monkeypatch) -> None:
    stub_batch_io(monkeypatch)
    failures = []
    monkeypatch.setattr(consensus, "fetch_sport_odds", lambda _key: ([{"id": "event"}], "249"))
    monkeypatch.setattr(
        consensus,
        "_store_sport_events",
        lambda *_args: (_ for _ in ()).throw(OperationalError("lost connection")),
    )
    monkeypatch.setattr(
        consensus,
        "_fail_batch_best_effort",
        lambda batch_id, error, summary: failures.append(
            (batch_id, str(error), summary["sports_succeeded"], summary["quota_remaining"])
        ),
    )

    with pytest.raises(OperationalError, match="lost connection"):
        consensus.collect_consensus_once(
            trigger="daily_heartbeat",
            sport_keys=["soccer_epl"],
            provider_quota_remaining=250,
        )
    assert failures == [(42, "lost connection", 0, 249)]


def test_partial_progress_and_quota_survive_a_later_database_failure(monkeypatch) -> None:
    stub_batch_io(monkeypatch)
    failures = []
    calls = []

    def fetch(key):
        calls.append(key)
        return ([{"id": key}], "150" if len(calls) == 1 else "100")

    def store(key, _events):
        if key == "soccer_spain_la_liga":
            raise OperationalError("lost connection")
        return 2, 0

    monkeypatch.setattr(consensus, "fetch_sport_odds", fetch)
    monkeypatch.setattr(consensus, "_store_sport_events", store)
    monkeypatch.setattr(
        consensus,
        "_fail_batch_best_effort",
        lambda _batch_id, _error, summary: failures.append(dict(summary)),
    )

    with pytest.raises(OperationalError, match="lost connection"):
        consensus.collect_consensus_once(
            trigger="daily_heartbeat",
            sport_keys=["soccer_epl", "soccer_spain_la_liga"],
            provider_quota_remaining=200,
        )

    assert calls == ["soccer_epl", "soccer_spain_la_liga"]
    assert failures[0]["sports_succeeded"] == 1
    assert failures[0]["snapshots_inserted"] == 2
    assert failures[0]["quota_remaining"] == 100


def test_best_effort_finalize_marks_saved_progress_partial(monkeypatch) -> None:
    updates = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _sql, params):
            updates.append(params)

        def commit(self):
            return None

    summary = {
        "trigger": "daily_heartbeat",
        "sports_planned": 2,
        "sports_attempted": 2,
        "sports_succeeded": 1,
        "sport_keys": ["soccer_epl", "soccer_spain_la_liga"],
        "quota_remaining": 100,
        "quota_verified": True,
        "stop_reason": None,
        "errors": [],
        "snapshots_inserted": 2,
    }
    monkeypatch.setattr(consensus, "connect", lambda: Connection())

    consensus._fail_batch_best_effort(42, OperationalError("lost"), summary)

    assert updates[0][0] == "partial"
    assert updates[0][1] == 2
    assert updates[0][3].obj["sports_succeeded"] == 1
    assert updates[0][3].obj["quota_remaining"] == 100


def test_database_programming_error_stops_before_next_provider_request(monkeypatch) -> None:
    stub_batch_io(monkeypatch)
    calls = []
    monkeypatch.setattr(
        consensus,
        "fetch_sport_odds",
        lambda key: (calls.append(key) or [{"id": key}], "250"),
    )
    monkeypatch.setattr(
        consensus,
        "_store_sport_events",
        lambda *_args: (_ for _ in ()).throw(ProgrammingError("missing table")),
    )
    monkeypatch.setattr(consensus, "_fail_batch_best_effort", lambda *_args: None)

    with pytest.raises(ProgrammingError, match="missing table"):
        consensus.collect_consensus_once(
            trigger="daily_heartbeat",
            sport_keys=["soccer_epl", "soccer_spain_la_liga"],
            provider_quota_remaining=300,
        )
    assert calls == ["soccer_epl"]


def test_invalid_budget_configuration_fails_closed(monkeypatch) -> None:
    reservations = []
    monkeypatch.setenv("ODDS_API_MONTHLY_REQUEST_BUDGET", "40O")
    monkeypatch.setattr(consensus, "_reserve_request", lambda *_args: reservations.append(True))

    with pytest.raises(RuntimeError, match="must be an integer"):
        consensus.collect_consensus_once(
            trigger="daily_heartbeat",
            sport_keys=["soccer_epl"],
            provider_quota_remaining=250,
        )
    assert reservations == []
