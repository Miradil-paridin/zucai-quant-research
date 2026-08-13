from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import connect, initialize_database  # noqa: E402
from app.jingcai.consensus import collect_consensus_once  # noqa: E402
from app.jingcai.consensus_gate import should_collect_now  # noqa: E402

LOG_PATH = BACKEND_ROOT.parent / "output" / "runtime" / "consensus_collector.log"


def main() -> int:
    started = datetime.now(timezone.utc).isoformat()
    try:
        initialize_database()
        with connect() as gate_connection:
            proceed, reason = should_collect_now(gate_connection)
        if not proceed:
            record = {"at_utc": started, "status": "skipped", "reason": reason}
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            return 0
        summary = collect_consensus_once()
        record = {"at_utc": started, "status": "ok", "trigger": reason, **summary}
        code = 0
    except Exception as error:
        record = {
            "at_utc": started,
            "status": "error",
            "error": f"{type(error).__name__}: {error}",
            "trace": traceback.format_exc(limit=3),
        }
        code = 1
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    if sys.stdout is not None:  # pythonw.exe 无控制台时跳过打印
        print(json.dumps(record, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
