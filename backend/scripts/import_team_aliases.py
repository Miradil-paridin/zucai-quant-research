from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from psycopg.types.json import Jsonb


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.database import connect, initialize_database  # noqa: E402
from app.historical.football_data_couk import normalize_team_name  # noqa: E402


class AliasImportError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import independently verified cross-source team aliases.")
    parser.add_argument(
        "--aliases",
        type=Path,
        default=PROJECT_ROOT / "data" / "source_samples" / "team_aliases_2024_25.json",
    )
    return parser.parse_args()


def import_aliases(path: Path) -> dict[str, object]:
    payload_bytes = path.read_bytes()
    payload = json.loads(payload_bytes)
    aliases = payload.get("aliases") or []
    if payload.get("contains_authentication_secret") is not False:
        raise AliasImportError("alias file must explicitly declare that it contains no authentication secret")
    if int(payload.get("record_count") or -1) != len(aliases):
        raise AliasImportError("alias record_count does not match the aliases array")

    report_path = path.parent / payload["evidence_report"]
    report_sha256 = hashlib.sha256(report_path.read_bytes()).hexdigest()
    if report_sha256.lower() != str(payload["evidence_report_sha256"]).lower():
        raise AliasImportError("cross-source evidence report hash does not match alias manifest")

    initialize_database()
    imported = 0
    with connect() as connection:
        for alias in aliases:
            if alias.get("source_name") != "football-data.org":
                raise AliasImportError(f"unexpected alias source {alias.get('source_name')!r}")
            target = connection.execute(
                """
                SELECT team_id
                FROM core.team_aliases
                WHERE source_name = 'football-data.co.uk'
                  AND competition_code = %s
                  AND source_alias = %s
                """,
                (alias["competition_code"], alias["canonical_name"]),
            ).fetchall()
            if len(target) != 1:
                raise AliasImportError(
                    f"canonical target did not resolve uniquely: "
                    f"{alias['competition_code']} {alias['canonical_name']!r}"
                )
            connection.execute(
                """
                INSERT INTO core.team_aliases (
                    team_id, source_name, competition_code, source_alias,
                    normalized_alias, verified, verification_method,
                    evidence, verified_at_utc
                )
                VALUES (%s, %s, %s, %s, %s, true, %s, %s, now())
                ON CONFLICT (source_name, competition_code, normalized_alias)
                DO UPDATE SET
                    team_id = EXCLUDED.team_id,
                    source_alias = EXCLUDED.source_alias,
                    verified = true,
                    verification_method = EXCLUDED.verification_method,
                    evidence = EXCLUDED.evidence,
                    verified_at_utc = now()
                """,
                (
                    int(target[0]["team_id"]),
                    alias["source_name"],
                    alias["competition_code"],
                    alias["source_alias"],
                    normalize_team_name(alias["source_alias"]),
                    alias["validation_basis"],
                    Jsonb({
                        **alias,
                        "alias_file_sha256": hashlib.sha256(payload_bytes).hexdigest(),
                    }),
                ),
            )
            imported += 1
        connection.commit()
    return {
        "status": "succeeded",
        "aliases_imported": imported,
        "alias_file_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "evidence_report_sha256": report_sha256,
    }


def main() -> int:
    result = import_aliases(parse_args().aliases.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
