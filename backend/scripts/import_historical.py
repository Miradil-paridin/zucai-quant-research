from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.historical.service import (  # noqa: E402
    HistoricalIngestionService,
    select_leagues,
    select_seasons,
)
from app.historical.football_data_couk import all_verified_seasons  # noqa: E402
from app.database import initialize_database  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download, validate and optionally persist authentic football-data.co.uk history."
    )
    parser.add_argument(
        "--leagues",
        nargs="+",
        default=["E0", "SP1", "D1", "I1", "F1"],
        help="Football-Data division codes. Default: five major leagues.",
    )
    parser.add_argument(
        "--seasons",
        nargs="+",
        default=["2122", "2223", "2324", "2425", "2526"],
        help=(
            "Season path codes (for example 9394 or 2425). "
            "Default: 2021/22 through 2025/26."
        ),
    )
    parser.add_argument(
        "--all-history",
        action="store_true",
        help="Import the verified archive range 1993/94 through 2025/26.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Validate/import existing cached files without network requests.",
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
    service = HistoricalIngestionService(PROJECT_ROOT / "data" / "historical")
    seasons = all_verified_seasons() if args.all_history else select_seasons(args.seasons)
    report = await service.run(
        leagues=select_leagues(args.leagues),
        seasons=seasons,
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
