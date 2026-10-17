"""Refresh the rolling official Jingcai result window without backfill state."""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.collector_runtime import append_jsonl  # noqa: E402
from app.database import connect  # noqa: E402
from scripts.backfill_jc_results import SLEEP_BASE, WINDOW_DAYS, _fetch, _upsert  # noqa: E402


LOG_PATH = BACKEND_ROOT.parent / "output" / "runtime" / "jc_results_collector.log"


def collect_recent_results(*, today: date | None = None, lookback_days: int = 4) -> dict:
    local_today = today or datetime.now().astimezone().date()
    start = local_today - timedelta(days=max(2, lookback_days))
    end = local_today - timedelta(days=1)
    rows_upserted = 0
    requests = 0
    with connect() as connection:
        cursor = start
        while cursor <= end:
            window_end = min(cursor + timedelta(days=WINDOW_DAYS - 1), end)
            page_no = pages = 1
            while page_no <= pages:
                value = _fetch(cursor, window_end, page_no)
                requests += 1
                pages = int(value.get("pages") or 1)
                for record in value.get("matchResult") or []:
                    if record.get("matchId") is None:
                        continue
                    _upsert(connection, record)
                    rows_upserted += 1
                page_no += 1
                if page_no <= pages:
                    time.sleep(SLEEP_BASE)
            cursor = window_end + timedelta(days=1)
        connection.commit()
    return {
        "at_utc": datetime.now(UTC).isoformat(),
        "status": "ok",
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "requests": requests,
        "rows_upserted": rows_upserted,
    }


def main() -> int:
    try:
        result = collect_recent_results()
    except Exception as error:
        result = {
            "at_utc": datetime.now(UTC).isoformat(),
            "status": "error",
            "error": f"{type(error).__name__}: {error}"[:500],
        }
        append_jsonl(LOG_PATH, result)
        return 1
    append_jsonl(LOG_PATH, result)
    if sys.stdout is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
