"""Backfill authoritative UCL fixtures/results, retaining raw response evidence.

No historical Jingcai odds or sale listings are synthesized. Existing fallback
fixtures are promoted by exact names and kickoff through Repository.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import connect  # noqa: E402
from app.data_sources import normalize_football_data, supported_football_data  # noqa: E402
from app.repository import repository  # noqa: E402


def persist_ucl(payload: dict, *, source_url: str, request_count: int = 1) -> dict:
    matches = payload.get("matches", [])
    if not matches:
        raise ValueError("UCL response contains no matches; nothing was marked complete")
    if any(m.get("competition", {}).get("code") != "CL" or not supported_football_data(m) for m in matches):
        raise ValueError("Unexpected competition in UCL response")
    batch_id = repository.start_batch("football-data.org", "ucl_backfill")
    try:
        with connect() as connection:
            # The legacy sale-only competition predates CL primary-source support.
            old = connection.execute("SELECT competition_id FROM core.competitions WHERE code='JC-UCL'").fetchone()
            canonical = connection.execute("SELECT competition_id FROM core.competitions WHERE code='CL'").fetchone()
            if old and canonical and old["competition_id"] != canonical["competition_id"]:
                raise ValueError("Both CL and JC-UCL exist; reconcile identities before importing")
            if old and not canonical:
                connection.execute("UPDATE core.competitions SET code='CL' WHERE competition_id=%s", (old["competition_id"],))
            repository.store_raw_payload(connection, batch_id=batch_id,
                source_name="football-data.org", resource_type="ucl_backfill",
                payload={"source_url": source_url, "response": payload})
            ids = [repository.upsert_fixture(connection, normalize_football_data(m), "football-data.org") for m in matches]
            if len(set(ids)) != len(matches):
                raise ValueError("Multiple provider events resolved to the same local match")
        repository.finish_batch(batch_id, request_count=request_count, record_count=len(ids))
        return {"batch_id": batch_id, "fixtures_upserted": len(ids), "match_ids": ids,
                "source_url": source_url, "historical_odds_created": 0}
    except Exception as error:
        repository.fail_batch(batch_id, error, request_count=request_count)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date_from", type=date.fromisoformat)
    parser.add_argument("date_to", type=date.fromisoformat)
    parser.add_argument("--cached-response", type=Path)
    args = parser.parse_args()
    if args.date_to < args.date_from:
        parser.error("date_to must not precede date_from")
    url = "https://api.football-data.org/v4/competitions/CL/matches"
    params = {"dateFrom": args.date_from.isoformat(), "dateTo": args.date_to.isoformat()}
    source_url = url + "?dateFrom=" + params["dateFrom"] + "&dateTo=" + params["dateTo"]
    if args.cached_response:
        payload = json.loads(args.cached_response.read_text(encoding="utf-8"))
    else:
        from curl_cffi import requests
        response = requests.get(url, params=params, headers={"X-Auth-Token": os.environ["FOOTBALL_DATA_API_KEY"]},
                                timeout=25, impersonate="chrome")
        response.raise_for_status()
        payload = response.json()
    print(json.dumps(persist_ucl(payload, source_url=source_url, request_count=0 if args.cached_response else 1), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
