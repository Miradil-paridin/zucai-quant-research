from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .football_data_nordic import (
    NORDIC_SPECS,
    FootballDataNordicAdapter,
)
from .repository import HistoricalRepository, historical_repository


class NordicHistoricalIngestionService:
    def __init__(
        self,
        data_root: Path,
        repository: HistoricalRepository = historical_repository,
    ) -> None:
        self.data_root = data_root
        self.adapter = FootballDataNordicAdapter(
            data_root / "raw" / "football-data.co.uk"
        )
        self.repository = repository

    async def run(
        self,
        *,
        offline: bool = False,
        persist: bool = True,
        force_download: bool = False,
    ) -> dict[str, Any]:
        started_at = datetime.now(UTC)
        report: dict[str, Any] = {
            "source": self.adapter.name,
            "dataset": "Nordic calendar leagues",
            "started_at_utc": started_at.isoformat(),
            "requested_files": len(NORDIC_SPECS),
            "files": [],
            "errors": [],
            "persisted": persist,
        }
        import_run_id = (
            self.repository.start_import_run(len(NORDIC_SPECS))
            if persist else None
        )
        imported_files = 0
        matches = 0
        odds = 0
        request_delay = max(
            0.25,
            float(os.getenv("HISTORICAL_REQUEST_DELAY_SECONDS", "0.5")),
        )
        report["request_delay_seconds"] = request_delay

        try:
            async with httpx.AsyncClient(timeout=45) as client:
                for index, spec in enumerate(NORDIC_SPECS):
                    try:
                        downloaded = (
                            self.adapter.local_file(spec)
                            if offline
                            else await self.adapter.download(
                                client,
                                spec,
                                force=force_download,
                            )
                        )
                        parsed = self.adapter.parse(downloaded)
                        database_result = (
                            self.repository.persist_file(parsed)
                            if persist else None
                        )
                        file_matches = len(parsed.records)
                        file_odds = sum(len(record.odds) for record in parsed.records)
                        matches += file_matches
                        odds += file_odds
                        imported_files += 1
                        report["files"].append({
                            "division": spec.league.source_division,
                            "url": spec.url,
                            "path": str(downloaded.path),
                            "sha256": downloaded.content_sha256,
                            "content_bytes": downloaded.content_bytes,
                            "http_etag": downloaded.http_etag,
                            "http_last_modified": downloaded.http_last_modified,
                            "retrieved_from_network": downloaded.retrieved_from_network,
                            "validation": parsed.validation_report,
                            "database": database_result,
                        })
                    except Exception as error:
                        report["errors"].append({
                            "division": spec.league.source_division,
                            "url": spec.url,
                            "message": str(error)[:500],
                        })
                    if not offline and index + 1 < len(NORDIC_SPECS):
                        await asyncio.sleep(request_delay)
        finally:
            report["finished_at_utc"] = datetime.now(UTC).isoformat()
            report["imported_files"] = imported_files
            report["matches_upserted"] = matches
            report["odds_upserted"] = odds
            report["status"] = (
                "succeeded"
                if not report["errors"]
                else "partial"
                if imported_files
                else "failed"
            )
            manifest_path = self._write_manifest(report)
            report["manifest_path"] = str(manifest_path)
            if import_run_id is not None:
                self.repository.finish_import_run(
                    import_run_id,
                    imported_files=imported_files,
                    matches_upserted=matches,
                    odds_upserted=odds,
                    report=report,
                    status=report["status"],
                    error_summary=(
                        "; ".join(item["message"] for item in report["errors"])[:1000]
                        or None
                    ),
                )
        return report

    def _write_manifest(self, report: dict[str, Any]) -> Path:
        audit_dir = self.data_root / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = audit_dir / f"football-data-nordic-{timestamp}.json"
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path
