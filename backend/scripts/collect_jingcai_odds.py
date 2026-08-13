from __future__ import annotations

import json
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from psycopg import Error as PsycopgError


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.collector_runtime import (  # noqa: E402
    append_jsonl,
    clear_database_backoff,
    database_backoff_until,
    record_database_failure,
    record_runtime_outcome,
)
from app.database import connect  # noqa: E402
from app.jingcai.collector import CollectorBlockedError, collect_once  # noqa: E402


RUNTIME_DIR = BACKEND_ROOT.parent / "output" / "runtime"
LOG_PATH = RUNTIME_DIR / "jingcai_collector.log"
STATE_PATH = RUNTIME_DIR / "jingcai_collector.state.json"
DB_ERRORS = (PsycopgError,)


def _emit(record: dict[str, object]) -> None:
    append_jsonl(LOG_PATH, record)
    if sys.stdout is not None:  # pythonw.exe has no console.
        print(json.dumps(record, ensure_ascii=False, indent=2))


def main() -> int:
    started_at = datetime.now(UTC)
    started = started_at.isoformat()
    next_probe = database_backoff_until(STATE_PATH, now=started_at)
    if next_probe:
        record = {
            "at_utc": started,
            "status": "db_backoff",
            "next_db_probe_at_utc": next_probe.isoformat(),
        }
        record_runtime_outcome(STATE_PATH, "db_backoff", at=started_at, attempted=False)
        _emit(record)
        return 0

    try:
        with connect() as connection:
            connection.execute("SELECT 1")
        summary = collect_once()
        record = {"at_utc": started, "status": "ok", **summary}
        record_runtime_outcome(STATE_PATH, "ok", at=started_at, succeeded=True)
        _emit(record)
        return 0
    except DB_ERRORS as error:
        state = record_database_failure(STATE_PATH, error, at=started_at)
        record = {
            "at_utc": started,
            "status": "db_unavailable",
            "error": f"{type(error).__name__}: {error}",
            "next_db_probe_at_utc": state["next_db_probe_at_utc"],
        }
        _emit(record)
        return 1
    except CollectorBlockedError as error:
        clear_database_backoff(STATE_PATH)
        error_text = str(error)
        record = {"at_utc": started, "status": "blocked", "error": error_text}
        record_runtime_outcome(STATE_PATH, "blocked", at=started_at, error=error_text)
        _emit(record)
        return 2
    except Exception as error:
        clear_database_backoff(STATE_PATH)
        error_text = f"{type(error).__name__}: {error}"
        record = {
            "at_utc": started,
            "status": "error",
            "error": error_text,
            "trace": traceback.format_exc(limit=4),
        }
        record_runtime_outcome(STATE_PATH, "error", at=started_at, error=error_text)
        _emit(record)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
