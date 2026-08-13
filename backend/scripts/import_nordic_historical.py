from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import initialize_database  # noqa: E402
from app.historical.nordic_service import NordicHistoricalIngestionService  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download, quarantine known risky odds, and optionally persist "
            "authentic Finland/Norway/Sweden historical results."
        )
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Validate/import the three cached source files without network requests.",
    )
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Download and validate only; do not connect to PostgreSQL.",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Replace cached content if the official source currently differs.",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    if not args.no_db:
        initialize_database()
    service = NordicHistoricalIngestionService(PROJECT_ROOT / "data" / "historical")
    report = await service.run(
        offline=args.offline,
        persist=not args.no_db,
        force_download=args.force_download,
    )
    print(json.dumps({
        "status": report["status"],
        "requested_files": report["requested_files"],
        "imported_files": report["imported_files"],
        "matches_upserted": report["matches_upserted"],
        "odds_upserted": report["odds_upserted"],
        "errors": report["errors"],
        "manifest_path": report["manifest_path"],
    }, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
