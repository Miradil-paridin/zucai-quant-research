from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import initialize_database  # noqa: E402
from app.jingcai.repricing_lag import analyze_repricing_lag  # noqa: E402

OUT_PATH = BACKEND_ROOT.parent / "output" / "runtime" / "repricing_lag_latest.json"
HISTORY_PATH = BACKEND_ROOT.parent / "output" / "runtime" / "repricing_lag_weekly.log"


def main() -> int:
    started = datetime.now(timezone.utc).isoformat()
    try:
        initialize_database()
        result = analyze_repricing_lag()
        result["generated_at_utc"] = started
        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUT_PATH.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        summary = {
            "at_utc": started,
            "matches": result["matches_with_both_sides"],
            "pairs": result["aligned_snapshot_pairs"],
            "signals": result["signals"],
            "clv_mean": result["clv_mean"],
            "clv_positive_share": result["clv_positive_share"],
        }
        with HISTORY_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(summary, ensure_ascii=False) + "\n")
        if sys.stdout is not None:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        with HISTORY_PATH.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {"at_utc": started, "status": "error",
                     "trace": traceback.format_exc(limit=3)},
                    ensure_ascii=False,
                )
                + "\n"
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
