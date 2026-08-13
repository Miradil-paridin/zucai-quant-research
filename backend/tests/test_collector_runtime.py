from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from app.collector_runtime import (
    append_jsonl,
    clear_database_backoff,
    database_backoff_until,
    load_runtime_state,
    record_database_failure,
    record_runtime_outcome,
)


def test_database_backoff_increases_and_clears(tmp_path) -> None:
    state_path = tmp_path / "collector.state.json"
    started = datetime(2026, 8, 13, 2, 0, tzinfo=UTC)

    first = record_database_failure(state_path, RuntimeError("db down"), at=started)
    assert first["consecutive_db_failures"] == 1
    assert database_backoff_until(state_path, now=started) == started + timedelta(minutes=15)

    second = record_database_failure(
        state_path, RuntimeError("still down"), at=started + timedelta(minutes=15)
    )
    assert second["consecutive_db_failures"] == 2
    assert database_backoff_until(
        state_path, now=started + timedelta(minutes=15)
    ) == started + timedelta(minutes=45)

    clear_database_backoff(state_path)
    state = load_runtime_state(state_path)
    assert state["consecutive_db_failures"] == 0
    assert state["next_db_probe_at_utc"] is None


def test_database_backoff_is_capped_at_six_hours(tmp_path) -> None:
    state_path = tmp_path / "collector.state.json"
    current = datetime(2026, 8, 13, 2, 0, tzinfo=UTC)
    delay = timedelta(0)
    for _ in range(10):
        state = record_database_failure(state_path, RuntimeError("db down"), at=current)
        next_probe = datetime.fromisoformat(state["next_db_probe_at_utc"])
        delay = next_probe - current
        current = next_probe
    assert delay == timedelta(hours=6)


def test_runtime_state_tolerates_corrupt_json_and_records_success(tmp_path) -> None:
    state_path = tmp_path / "collector.state.json"
    state_path.write_text("not-json", encoding="utf-8")
    assert load_runtime_state(state_path) == {}

    at = datetime(2026, 8, 13, 3, 0, tzinfo=UTC)
    state = record_runtime_outcome(state_path, "ok", at=at, succeeded=True)
    assert state["last_success_at_utc"] == at.isoformat()
    assert state["last_status"] == "ok"


def test_database_failure_tolerates_corrupt_failure_count(tmp_path) -> None:
    state_path = tmp_path / "collector.state.json"
    state_path.write_text('{"consecutive_db_failures": "bad"}', encoding="utf-8")
    state = record_database_failure(state_path, RuntimeError("db down"))
    assert state["consecutive_db_failures"] == 1


def test_failure_backoff_keeps_the_actionable_error(tmp_path) -> None:
    state_path = tmp_path / "collector.state.json"
    at = datetime(2026, 8, 13, 2, 0, tzinfo=UTC)
    record_database_failure(state_path, RuntimeError("db down"), at=at)

    state = record_runtime_outcome(
        state_path,
        "failure_backoff",
        at=at + timedelta(hours=1),
        reason="failure_backoff(1.0h)",
        attempted=False,
    )

    assert state["last_status"] == "failure_backoff"
    assert state["last_reason"] == "failure_backoff(1.0h)"
    assert state["last_error"] == "RuntimeError: db down"


def test_append_jsonl_rotates_and_keeps_valid_json_lines(tmp_path) -> None:
    log_path = tmp_path / "collector.log"
    append_jsonl(log_path, {"run": 1, "payload": "x" * 20}, max_bytes=60, backups=2)
    append_jsonl(log_path, {"run": 2, "payload": "y" * 20}, max_bytes=60, backups=2)

    current = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    previous = [
        json.loads(line)
        for line in (tmp_path / "collector.log.1").read_text(encoding="utf-8").splitlines()
    ]
    assert current == [{"run": 2, "payload": "y" * 20}]
    assert previous == [{"run": 1, "payload": "x" * 20}]
