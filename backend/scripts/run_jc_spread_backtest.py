from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import initialize_database  # noqa: E402
from app.jingcai.spread_backtest import backtest_spread  # noqa: E402

OUT_PATH = BACKEND_ROOT.parent / "output" / "runtime" / "jc_spread_backtest.json"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    start_on = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2024, 7, 1)
    end_on = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date(2026, 6, 30)
    initialize_database()
    result = backtest_spread(start_on, end_on)
    result["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
