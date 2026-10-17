from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from psycopg import Error as PsycopgError  # noqa: E402
from app.collector_runtime import (  # noqa: E402
    append_jsonl, record_database_failure, record_runtime_outcome, _write_runtime_state,
)
from app.jingcai.repricing_lag import analyze_repricing_lag  # noqa: E402

OUT_PATH = BACKEND_ROOT.parent / "output" / "runtime" / "repricing_lag_latest.json"
HISTORY_PATH = BACKEND_ROOT.parent / "output" / "runtime" / "repricing_lag_weekly.log"
STATE_PATH = BACKEND_ROOT.parent / "output" / "runtime" / "repricing_lag.state.json"


def main() -> int:
    started = datetime.now(timezone.utc).isoformat()
    try:
        result = analyze_repricing_lag()
        result["generated_at_utc"] = started
        _write_runtime_state(OUT_PATH, result)
        summary = {
            "at_utc": started,
            "matches": result["matches_with_both_sides"],
            "pairs": result["aligned_snapshot_pairs"],
            "signals": result["signals"],
            "clv_mean": result["clv_mean"],
            "clv_positive_share": result["clv_positive_share"],
        }
        append_jsonl(HISTORY_PATH, summary)
        record_runtime_outcome(STATE_PATH, "ok", succeeded=True)
        if sys.stdout is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        if isinstance(error, PsycopgError):
            record_database_failure(STATE_PATH, error)
        else:
            record_runtime_outcome(STATE_PATH, "error", error=f"{type(error).__name__}: {error}")
        append_jsonl(HISTORY_PATH, {"at_utc": started, "status": "error",
                                   "trace": traceback.format_exc(limit=3)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
