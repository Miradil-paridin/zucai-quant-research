from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import httpx

from .football_data_couk import (
    BIG_FIVE_LEAGUES,
    DEFAULT_SEASONS,
    DownloadedSourceFile,
    FootballDataCoUkAdapter,
    LeagueDefinition,
    SeasonDefinition,
    SourceFileSpec,
    specs_for,
)
from .repository import HistoricalRepository, historical_repository


class HistoricalIngestionService:
    def __init__(
        self,
        data_root: Path,
        repository: HistoricalRepository = historical_repository,
    ) -> None:
        self.data_root = data_root
        self.adapter = FootballDataCoUkAdapter(data_root / "raw" / "football-data.co.uk")
        self.repository = repository

    async def run(
        self,
        *,
        leagues: Iterable[LeagueDefinition] = BIG_FIVE_LEAGUES,
        seasons: Iterable[SeasonDefinition] = DEFAULT_SEASONS,
        offline: bool = False,
        persist: bool = True,
        force_download: bool = False,
    ) -> dict[str, Any]:
        requested_specs = specs_for(tuple(leagues), tuple(seasons))
        started_at = datetime.now(UTC)
        report: dict[str, Any] = {
            "source": self.adapter.name,
            "started_at_utc": started_at.isoformat(),
            "requested_files": len(requested_specs),
            "files": [],
            "errors": [],
            "persisted": persist,
        }
        import_run_id = self.repository.start_import_run(len(requested_specs)) if persist else None
        imported_files = 0
        match_count = 0
        odds_count = 0
        request_delay_seconds = max(
            0.25,
            float(os.getenv("HISTORICAL_REQUEST_DELAY_SECONDS", "0.5")),
        )
        report["request_delay_seconds"] = request_delay_seconds

        try:
            async with httpx.AsyncClient(timeout=45) as client:
                for index, spec in enumerate(requested_specs):
                    try:
                        downloaded = (
                            self._local_file(spec)
                            if offline
                            else await self.adapter.download(client, spec, force=force_download)
                        )
                        parsed = self.adapter.parse(downloaded)
                        file_result: dict[str, Any] = {
                            "url": spec.url,
                            "path": str(downloaded.path),
                            "sha256": downloaded.content_sha256,
                            "content_bytes": downloaded.content_bytes,
                            "http_etag": downloaded.http_etag,
                            "http_last_modified": downloaded.http_last_modified,
                            "retrieved_from_network": downloaded.retrieved_from_network,
                            "validation": parsed.validation_report,
                        }
                        if persist:
                            persisted = self.repository.persist_file(parsed)
                            file_result["database"] = persisted
                            match_count += persisted["matches_upserted"]
                            odds_count += persisted["odds_upserted"]
                        else:
                            match_count += len(parsed.records)
                            odds_count += sum(len(record.odds) for record in parsed.records)
                        imported_files += 1
                        report["files"].append(file_result)
                    except Exception as error:
                        report["errors"].append({
                            "url": spec.url,
                            "division": spec.league.source_division,
                            "season": spec.season.label,
                            "message": str(error)[:500],
                        })
                    if not offline and index + 1 < len(requested_specs):
                        await asyncio.sleep(request_delay_seconds)
        finally:
            report["finished_at_utc"] = datetime.now(UTC).isoformat()
            report["imported_files"] = imported_files
            report["matches_upserted"] = match_count
            report["odds_upserted"] = odds_count
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
                    matches_upserted=match_count,
                    odds_upserted=odds_count,
                    report={**report, "files": [
                        {
                            "url": item["url"],
                            "path": item["path"],
                            "sha256": item["sha256"],
                            "http_etag": item["http_etag"],
                            "http_last_modified": item["http_last_modified"],
                            "retrieved_from_network": item["retrieved_from_network"],
                            "validation": item["validation"],
                            "database": item.get("database"),
                        }
                        for item in report["files"]
                    ]},
                    status=report["status"],
                    error_summary="; ".join(error["message"] for error in report["errors"])[:1000] or None,
                )
        return report

    def _local_file(self, spec: SourceFileSpec) -> DownloadedSourceFile:
        path = self.adapter.data_root / spec.season.source_code / f"{spec.league.source_division}.csv"
        if not path.exists():
            raise FileNotFoundError(f"offline source file does not exist: {path}")
        content = path.read_bytes()
        self.adapter._validate_download(spec, content, "text/csv")
        return DownloadedSourceFile(
            spec=spec,
            path=path,
            content_sha256=hashlib.sha256(content).hexdigest(),
            content_bytes=len(content),
            http_etag=None,
            http_last_modified=None,
            retrieved_from_network=False,
        )

    def _write_manifest(self, report: dict[str, Any]) -> Path:
        audit_dir = self.data_root / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = audit_dir / f"football-data-couk-{timestamp}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def select_leagues(codes: Iterable[str]) -> tuple[LeagueDefinition, ...]:
    requested = {value.upper() for value in codes}
    selected = tuple(item for item in BIG_FIVE_LEAGUES if item.source_division in requested)
    missing = requested - {item.source_division for item in selected}
    if missing:
        raise ValueError(f"unsupported league codes: {sorted(missing)}")
    return selected


def select_seasons(codes: Iterable[str]) -> tuple[SeasonDefinition, ...]:
    from .football_data_couk import season_from_code

    selected: list[SeasonDefinition] = []
    seen: set[str] = set()
    for code in codes:
        season = season_from_code(code)
        if season.source_code not in seen:
            selected.append(season)
            seen.add(season.source_code)
    if not selected:
        raise ValueError("at least one season code is required")
    return tuple(selected)
