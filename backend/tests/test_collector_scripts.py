from __future__ import annotations

import json
from types import SimpleNamespace

from psycopg import OperationalError

from app.collector_runtime import load_runtime_state
from scripts import collect_consensus_odds, collect_core_data, collect_jingcai_odds


class ProbeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args, **_kwargs):
        return self

    def close(self):
        return None


def _old_failure_state(path, failures: int = 5) -> None:
    path.write_text(
        json.dumps(
            {
                "consecutive_db_failures": failures,
                "next_db_probe_at_utc": "2000-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )


def test_jingcai_write_failure_continues_existing_database_backoff(
    monkeypatch,
    tmp_path,
) -> None:
    state_path = tmp_path / "jingcai.state.json"
    _old_failure_state(state_path)
    monkeypatch.setattr(collect_jingcai_odds, "STATE_PATH", state_path)
    monkeypatch.setattr(collect_jingcai_odds, "LOG_PATH", tmp_path / "jingcai.log")
    monkeypatch.setattr(collect_jingcai_odds, "connect", lambda: ProbeConnection())
    monkeypatch.setattr(
        collect_jingcai_odds,
        "collect_once",
        lambda: (_ for _ in ()).throw(OperationalError("write unavailable")),
    )

    assert collect_jingcai_odds.main() == 1
    assert load_runtime_state(state_path)["consecutive_db_failures"] == 6


def test_consensus_downstream_failure_continues_existing_database_backoff(
    monkeypatch,
    tmp_path,
) -> None:
    state_path = tmp_path / "consensus.state.json"
    _old_failure_state(state_path)
    connection = ProbeConnection()
    decision = SimpleNamespace(
        proceed=True,
        reason="daily_heartbeat",
        sport_keys=("soccer_epl",),
        quota_remaining=250,
        quota_verified=True,
        monthly_requests=10,
    )
    monkeypatch.setattr(collect_consensus_odds, "STATE_PATH", state_path)
    monkeypatch.setattr(collect_consensus_odds, "LOG_PATH", tmp_path / "consensus.log")
    monkeypatch.setattr(collect_consensus_odds, "connect", lambda **_kwargs: connection)
    monkeypatch.setattr(collect_consensus_odds, "acquire_collection_lock", lambda _conn: True)
    monkeypatch.setattr(collect_consensus_odds, "release_collection_lock", lambda _conn: None)
    monkeypatch.setattr(collect_consensus_odds, "ensure_jingcai_runtime_schema", lambda _conn: None)
    monkeypatch.setattr(collect_consensus_odds, "finalize_stale_collection_runs", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(collect_consensus_odds, "should_collect_now", lambda *_args, **_kwargs: decision)
    monkeypatch.setattr(
        collect_consensus_odds,
        "collect_consensus_once",
        lambda **_kwargs: (_ for _ in ()).throw(OperationalError("write unavailable")),
    )

    assert collect_consensus_odds.main() == 1
    assert load_runtime_state(state_path)["consecutive_db_failures"] == 6


def test_core_collector_write_failure_continues_existing_database_backoff(
    monkeypatch,
    tmp_path,
) -> None:
    state_path = tmp_path / "core.state.json"
    _old_failure_state(state_path)
    monkeypatch.setattr(collect_core_data, "STATE_PATH", state_path)
    monkeypatch.setattr(collect_core_data, "LOG_PATH", tmp_path / "core.log")
    monkeypatch.setattr(collect_core_data, "connect", lambda: ProbeConnection())

    async def fail_run(*_args, **_kwargs):
        raise OperationalError("write unavailable")

    monkeypatch.setattr(collect_core_data.collector, "run", fail_run)

    assert collect_core_data.main() == 1
    assert load_runtime_state(state_path)["consecutive_db_failures"] == 6
