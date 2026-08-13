from __future__ import annotations

import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import initialize_database  # noqa: E402
from app.research.service import run_default_backtest  # noqa: E402


def main() -> None:
    initialize_database()
    result = run_default_backtest()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
