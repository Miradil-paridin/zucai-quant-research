from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import initialize_database  # noqa: E402
from app.jingcai.analysis import analyze_spread  # noqa: E402

OUT_PATH = BACKEND_ROOT.parent / "output" / "runtime" / "jc_spread_latest.json"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    initialize_database()
    result = analyze_spread()
    result["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
