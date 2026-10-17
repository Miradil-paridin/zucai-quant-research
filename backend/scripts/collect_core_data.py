from __future__ import annotations

import asyncio
import json
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from psycopg import Error as PsycopgError


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.collector import collector  # noqa: E402
from app.collector_runtime import (  # noqa: E402
    append_jsonl,
    clear_database_backoff,
    database_backoff_until,
    record_database_failure,
    record_runtime_outcome,
)
from app.database import connect  # noqa: E402
from app.jingcai.core_fallback import ingest_current_fallback  # noqa: E402


RUNTIME_DIR = BACKEND_ROOT.parent / "output" / "runtime"
LOG_PATH = RUNTIME_DIR / "core_collector.log"
STATE_PATH = RUNTIME_DIR / "core_collector.state.json"
DB_ERRORS = (PsycopgError,)


def _emit(record: dict[str, object]) -> None:
    safe_record = json.loads(json.dumps(record, ensure_ascii=False, default=str))
    append_jsonl(LOG_PATH, safe_record)
    if sys.stdout is not None:
        print(json.dumps(safe_record, ensure_ascii=False, indent=2))


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
        summary = asyncio.run(collector.run(days=7))
        # Always merge the selected-sale fallback. Repository writes are
        # source-prioritized, so it fills gaps without replacing status, scores,
        # season dates, or names supplied by a primary fixture provider.
        summary.setdefault("resources", {})["sporttery_fallback"] = (
            ingest_current_fallback()
        )
        errors = summary.get("errors") or []
        status = "partial" if errors else "ok"
        record = {**summary, "at_utc": started, "status": status}
        clear_database_backoff(STATE_PATH)
        record_runtime_outcome(
            STATE_PATH,
            status,
            at=started_at,
            error="; ".join(str(item) for item in errors)[:500] or None,
            succeeded=not errors,
        )
        _emit(record)
        return 2 if errors else 0
    except DB_ERRORS as error:
        state = record_database_failure(STATE_PATH, error, at=started_at)
        _emit(
            {
                "at_utc": started,
                "status": "db_unavailable",
                "error": f"{type(error).__name__}: {error}",
                "next_db_probe_at_utc": state["next_db_probe_at_utc"],
            }
        )
        return 1
    except Exception as error:
        clear_database_backoff(STATE_PATH)
        error_text = f"{type(error).__name__}: {error}"
        record_runtime_outcome(STATE_PATH, "error", at=started_at, error=error_text)
        _emit(
            {
                "at_utc": started,
                "status": "error",
                "error": error_text,
                "trace": traceback.format_exc(limit=4),
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
