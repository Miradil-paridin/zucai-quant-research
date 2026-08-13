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
from app.jingcai.consensus import collect_consensus_once  # noqa: E402
from app.jingcai.consensus_gate import (  # noqa: E402
    acquire_collection_lock,
    finalize_stale_collection_runs,
    release_collection_lock,
    should_collect_now,
)
from app.jingcai.schema import ensure_jingcai_runtime_schema  # noqa: E402


RUNTIME_DIR = BACKEND_ROOT.parent / "output" / "runtime"
LOG_PATH = RUNTIME_DIR / "consensus_collector.log"
STATE_PATH = RUNTIME_DIR / "consensus_collector.state.json"
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

    lock_connection = None
    lock_acquired = False
    try:
        lock_connection = connect(autocommit=True)
        lock_connection.execute("SELECT 1")
        lock_acquired = acquire_collection_lock(lock_connection)
        if not lock_acquired:
            clear_database_backoff(STATE_PATH)
            record = {"at_utc": started, "status": "skipped", "reason": "collector_busy"}
            record_runtime_outcome(
                STATE_PATH,
                "skipped",
                at=started_at,
                reason="collector_busy",
                attempted=False,
            )
            _emit(record)
            return 0

        ensure_jingcai_runtime_schema(lock_connection)
        finalize_stale_collection_runs(lock_connection, now_utc=started_at)
        decision = should_collect_now(lock_connection, now_utc=started_at)
        if not decision.proceed:
            record = {
                "at_utc": started,
                "status": "skipped",
                "reason": decision.reason,
                "monthly_requests": decision.monthly_requests,
                "quota_remaining": decision.quota_remaining,
            }
            clear_database_backoff(STATE_PATH)
            preserving_failure = decision.reason.startswith(
                ("failure_backoff", "previous_run_incomplete")
            )
            runtime_status = "failure_backoff" if preserving_failure else "skipped"
            record_runtime_outcome(
                STATE_PATH,
                runtime_status,
                at=started_at,
                reason=decision.reason,
                attempted=not preserving_failure,
            )
            _emit(record)
            return 0

        summary = collect_consensus_once(
            trigger=decision.reason,
            sport_keys=decision.sport_keys,
            provider_quota_remaining=decision.quota_remaining,
            provider_quota_verified=decision.quota_verified,
        )
        run_status = str(summary["status"])
        log_status = "ok" if run_status == "succeeded" else run_status
        record = {"at_utc": started, **summary, "status": log_status, "run_status": run_status}
        succeeded = run_status in {"succeeded", "partial"}
        error_text = "; ".join(summary.get("errors") or []) or None
        clear_database_backoff(STATE_PATH)
        record_runtime_outcome(
            STATE_PATH,
            log_status,
            at=started_at,
            error=error_text,
            reason=summary.get("stop_reason"),
            succeeded=succeeded,
        )
        _emit(record)
        return 0 if run_status == "succeeded" else 2 if run_status == "partial" else 1
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
    finally:
        if lock_connection is not None:
            try:
                if lock_acquired:
                    try:
                        release_collection_lock(lock_connection)
                    except Exception:
                        pass
            finally:
                try:
                    lock_connection.close()
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
