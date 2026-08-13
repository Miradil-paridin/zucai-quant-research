from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


DB_BACKOFF_MINUTES = (15, 30, 60, 120, 240, 360)
DEFAULT_LOG_MAX_BYTES = 1024 * 1024
DEFAULT_LOG_BACKUPS = 5


def utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime:
    current = value or utc_now()
    if current.tzinfo is None:
        return current.replace(tzinfo=UTC)
    return current.astimezone(UTC)


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def load_runtime_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_runtime_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def database_backoff_until(path: Path, *, now: datetime | None = None) -> datetime | None:
    current = _as_utc(now)
    next_probe = _parse_datetime(load_runtime_state(path).get("next_db_probe_at_utc"))
    return next_probe if next_probe and next_probe > current else None


def clear_database_backoff(path: Path) -> dict[str, Any]:
    state = load_runtime_state(path)
    if state.get("consecutive_db_failures") or state.get("next_db_probe_at_utc"):
        state["consecutive_db_failures"] = 0
        state["next_db_probe_at_utc"] = None
        _write_runtime_state(path, state)
    return state


def record_database_failure(
    path: Path,
    error: BaseException,
    *,
    at: datetime | None = None,
) -> dict[str, Any]:
    current = _as_utc(at)
    state = load_runtime_state(path)
    try:
        previous_failures = int(state.get("consecutive_db_failures") or 0)
    except (TypeError, ValueError):
        previous_failures = 0
    failures = max(0, previous_failures) + 1
    delay_minutes = DB_BACKOFF_MINUTES[min(failures - 1, len(DB_BACKOFF_MINUTES) - 1)]
    state.update(
        {
            "last_attempt_at_utc": current.isoformat(),
            "last_status": "db_unavailable",
            "last_reason": "database_connection_failed",
            "last_error": f"{type(error).__name__}: {error}"[:500],
            "consecutive_db_failures": failures,
            "next_db_probe_at_utc": (current + timedelta(minutes=delay_minutes)).isoformat(),
        }
    )
    _write_runtime_state(path, state)
    return state


def record_runtime_outcome(
    path: Path,
    status: str,
    *,
    at: datetime | None = None,
    error: str | None = None,
    reason: str | None = None,
    succeeded: bool = False,
    attempted: bool = True,
) -> dict[str, Any]:
    current = _as_utc(at)
    state = load_runtime_state(path)
    if attempted:
        state["last_attempt_at_utc"] = current.isoformat()
    state["last_status"] = status
    if reason is not None:
        state["last_reason"] = reason[:500]
    elif attempted or succeeded:
        state["last_reason"] = None
    if error is not None:
        state["last_error"] = error[:500]
    elif attempted or succeeded:
        state["last_error"] = None
    if succeeded:
        state["last_success_at_utc"] = current.isoformat()
        state["consecutive_db_failures"] = 0
        state["next_db_probe_at_utc"] = None
    _write_runtime_state(path, state)
    return state


def append_jsonl(
    path: Path,
    record: dict[str, Any],
    *,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    backups: int = DEFAULT_LOG_BACKUPS,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    current_size = path.stat().st_size if path.exists() else 0
    if current_size and current_size + len(encoded) > max(1, max_bytes):
        for index in range(max(0, backups), 0, -1):
            source = path if index == 1 else path.with_name(f"{path.name}.{index - 1}")
            destination = path.with_name(f"{path.name}.{index}")
            if source.exists():
                os.replace(source, destination)
    with path.open("ab") as handle:
        handle.write(encoded)
