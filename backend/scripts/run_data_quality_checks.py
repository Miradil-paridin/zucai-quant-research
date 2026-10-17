from __future__ import annotations

import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.data_quality import run_data_quality_checks  # noqa: E402


def main() -> int:
    result = run_data_quality_checks()
    if sys.stdout is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
