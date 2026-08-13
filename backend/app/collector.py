from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .data_sources import API_FOOTBALL_URL, FOOTBALL_DATA_URL, normalize_api_football, normalize_football_data
from .localization import is_supported_competition
from .repository import Repository, repository


class CollectorBusyError(RuntimeError):
    pass


class IngestionCollector:
    def __init__(self, data_repository: Repository = repository) -> None:
        self.repository = data_repository
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._lock.locked()

    async def run(self, *, days: int = 7) -> dict[str, Any]:
        if self._lock.locked():
            raise CollectorBusyError("an ingestion run is already in progress")
        async with self._lock:
            return await self._run(days=max(1, min(days, 7)))

    async def _run(self, *, days: int) -> dict[str, Any]:
        started_at = datetime.now(UTC)
        local_today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        end_date = local_today + timedelta(days=days - 1)
        summary: dict[str, Any] = {
            "status": "succeeded",
            "started_at_utc": started_at,
            "date_from": local_today,
            "date_to": end_date,
            "api_football_requests": 0,
            "football_data_requests": 0,
            "resources": {},
            "errors": [],
        }
        api_key = os.getenv("API_FOOTBALL_API_KEY")
        football_data_key = os.getenv("FOOTBALL_DATA_API_KEY")

        async with httpx.AsyncClient(timeout=20) as client:
            quota = await self._api_football_quota(client, api_key)
            summary["quota_before"] = quota

            api_fixtures: list[dict[str, Any]] = []
            if api_key and quota.get("remaining", 0) > 0:
                batch_id = self.repository.start_batch("api-football", "fixtures")
                try:
                    response = await client.get(
                        f"{API_FOOTBALL_URL}/fixtures",
                        params={"date": local_today.isoformat(), "timezone": "UTC"},
                        headers={"x-apisports-key": api_key},
                    )
                    summary["api_football_requests"] += 1
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get("errors"):
                        raise RuntimeError(str(payload["errors"]))
                    api_fixtures = [
                        normalize_api_football(item)
                        for item in payload.get("response", [])
                        if is_supported_competition(item["league"].get("country"), item["league"].get("name", ""))
                    ]
                    resource = self.repository.persist_fixture_response(
                        batch_id=batch_id,
                        source="api-football",
                        payload=payload,
                        fixtures=api_fixtures,
                        source_entity_id=local_today.isoformat(),
                    )
                    self.repository.finish_batch(batch_id, request_count=1, record_count=len(api_fixtures))
                    summary["resources"]["api_football_fixtures"] = resource
                except Exception as error:
                    self.repository.fail_batch(batch_id, error, request_count=1)
                    summary["errors"].append({"resource": "api_football_fixtures", "message": str(error)[:300]})

            if football_data_key:
                batch_id = self.repository.start_batch("football-data.org", "fixtures")
                try:
                    response = await client.get(
                        f"{FOOTBALL_DATA_URL}/matches",
                        params={"dateFrom": local_today.isoformat(), "dateTo": end_date.isoformat()},
                        headers={"X-Auth-Token": football_data_key},
                    )
                    summary["football_data_requests"] += 1
                    response.raise_for_status()
                    payload = response.json()
                    football_data_fixtures = [
                        normalize_football_data(item)
                        for item in payload.get("matches", [])
                        if is_supported_competition(
                            ((item.get("competition") or {}).get("area") or {}).get("name"),
                            (item.get("competition") or {}).get("name", ""),
                        )
                    ]
                    resource = self.repository.persist_fixture_response(
                        batch_id=batch_id,
                        source="football-data.org",
                        payload=payload,
                        fixtures=football_data_fixtures,
                        source_entity_id=f"{local_today.isoformat()}..{end_date.isoformat()}",
                    )
                    self.repository.finish_batch(batch_id, request_count=1, record_count=len(football_data_fixtures))
                    summary["resources"]["football_data_fixtures"] = resource
                except Exception as error:
                    self.repository.fail_batch(batch_id, error, request_count=1)
                    summary["errors"].append({"resource": "football_data_fixtures", "message": str(error)[:300]})

            fixture_ids = [str(item["provider_ids"]["api-football"]) for item in api_fixtures]
            remaining = quota.get("remaining", 0) - summary["api_football_requests"]
            if fixture_ids and remaining >= 1 and self.repository.resource_due(
                "api-football", "injuries", minimum_age=timedelta(hours=12)
            ):
                await self._collect_api_resource(
                    client, api_key, "injuries", {"date": local_today.isoformat()}, summary,
                    lambda batch_id, payload, captured: self.repository.persist_injuries(
                        batch_id=batch_id, payload=payload, captured_at=captured
                    ),
                )
            remaining = quota.get("remaining", 0) - summary["api_football_requests"]
            if fixture_ids and remaining >= 2 and self.repository.resource_due(
                "api-football", "odds", minimum_age=timedelta(hours=6)
            ):
                await self._collect_paginated_odds(
                    client, api_key, local_today.isoformat(), set(fixture_ids), summary, max_requests=min(10, remaining - 1)
                )

            detail_ids = []
            now = datetime.now(UTC)
            for fixture in api_fixtures:
                kickoff = datetime.fromisoformat(fixture["kickoff_utc"].replace("Z", "+00:00"))
                if fixture["status"] != "scheduled" or kickoff <= now + timedelta(minutes=90):
                    detail_ids.append(str(fixture["provider_ids"]["api-football"]))
            remaining = quota.get("remaining", 0) - summary["api_football_requests"]
            if detail_ids and remaining >= 1:
                await self._collect_fixture_bundles(
                    client, api_key, detail_ids, summary, max_requests=min(len(detail_ids), remaining)
                )

        summary["finished_at_utc"] = datetime.now(UTC)
        if summary["errors"]:
            summary["status"] = "partial" if summary["resources"] else "failed"
        return summary

    async def _collect_api_resource(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        resource_name: str,
        params: dict[str, Any],
        summary: dict[str, Any],
        persist,
        *,
        endpoint: str | None = None,
    ) -> None:
        batch_id = self.repository.start_batch("api-football", resource_name)
        try:
            response = await client.get(
                f"{API_FOOTBALL_URL}/{endpoint or resource_name}",
                params=params,
                headers={"x-apisports-key": api_key},
            )
            summary["api_football_requests"] += 1
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                raise RuntimeError(str(payload["errors"]))
            captured_at = datetime.now(UTC)
            result = persist(batch_id, payload, captured_at)
            record_count = int(payload.get("results") or 0)
            self.repository.finish_batch(batch_id, request_count=1, record_count=record_count)
            summary["resources"][resource_name] = result
        except Exception as error:
            self.repository.fail_batch(batch_id, error, request_count=1)
            summary["errors"].append({"resource": resource_name, "message": str(error)[:300]})

    async def _collect_paginated_odds(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        requested_date: str,
        target_fixture_ids: set[str],
        summary: dict[str, Any],
        *,
        max_requests: int,
    ) -> None:
        batch_id = self.repository.start_batch("api-football", "odds")
        pages_read = 0
        result_count = 0
        aggregate = {"raw_inserted": 0, "odds_inserted": 0}
        found: set[str] = set()
        try:
            total_pages = 1
            while pages_read < min(total_pages, max_requests):
                page = pages_read + 1
                response = await client.get(
                    f"{API_FOOTBALL_URL}/odds",
                    params={"date": requested_date, "page": page},
                    headers={"x-apisports-key": api_key},
                )
                summary["api_football_requests"] += 1
                response.raise_for_status()
                payload = response.json()
                if payload.get("errors"):
                    raise RuntimeError(str(payload["errors"]))
                pages_read += 1
                total_pages = int((payload.get("paging") or {}).get("total") or 1)
                result_count += int(payload.get("results") or 0)
                for item in payload.get("response") or []:
                    fixture_id = str((item.get("fixture") or {}).get("id") or "")
                    if fixture_id in target_fixture_ids:
                        found.add(fixture_id)
                persisted = self.repository.persist_odds(
                    batch_id=batch_id, payload=payload, captured_at=datetime.now(UTC)
                )
                aggregate["raw_inserted"] += persisted["raw_inserted"]
                aggregate["odds_inserted"] += persisted["odds_inserted"]
                if found >= target_fixture_ids:
                    break
            status = "succeeded" if found >= target_fixture_ids or pages_read >= total_pages else "partial"
            self.repository.finish_batch(
                batch_id, request_count=pages_read, record_count=result_count, status=status
            )
            summary["resources"]["odds"] = {
                **aggregate,
                "pages_read": pages_read,
                "target_fixtures_found": len(found),
                "target_fixtures": len(target_fixture_ids),
            }
        except Exception as error:
            if pages_read:
                self.repository.finish_batch(
                    batch_id, request_count=pages_read, record_count=result_count, status="partial"
                )
            else:
                self.repository.fail_batch(batch_id, error, request_count=pages_read)
            summary["errors"].append({"resource": "odds", "message": str(error)[:300]})

    async def _collect_fixture_bundles(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        fixture_ids: list[str],
        summary: dict[str, Any],
        *,
        max_requests: int,
    ) -> None:
        batch_id = self.repository.start_batch("api-football", "fixture_bundle")
        requests_made = 0
        records = 0
        aggregate = {"raw_inserted": 0, "events": 0, "lineups": 0, "player_stats": 0}
        try:
            for fixture_id in fixture_ids[:max_requests]:
                response = await client.get(
                    f"{API_FOOTBALL_URL}/fixtures",
                    params={"id": fixture_id},
                    headers={"x-apisports-key": api_key},
                )
                summary["api_football_requests"] += 1
                requests_made += 1
                response.raise_for_status()
                payload = response.json()
                if payload.get("errors"):
                    raise RuntimeError(str(payload["errors"]))
                records += int(payload.get("results") or 0)
                persisted = self.repository.persist_fixture_bundle(
                    batch_id=batch_id, payload=payload, captured_at=datetime.now(UTC)
                )
                for key in aggregate:
                    aggregate[key] += persisted[key]
            self.repository.finish_batch(
                batch_id, request_count=requests_made, record_count=records,
                status="succeeded" if requests_made == len(fixture_ids) else "partial",
            )
            summary["resources"]["fixture_bundle"] = aggregate
        except Exception as error:
            if requests_made:
                self.repository.finish_batch(
                    batch_id, request_count=requests_made, record_count=records, status="partial"
                )
            else:
                self.repository.fail_batch(batch_id, error)
            summary["errors"].append({"resource": "fixture_bundle", "message": str(error)[:300]})

    async def _api_football_quota(self, client: httpx.AsyncClient, api_key: str | None) -> dict[str, Any]:
        if not api_key:
            return {"used": 0, "limit": 0, "remaining": 0, "status": "not_configured"}
        try:
            response = await client.get(f"{API_FOOTBALL_URL}/status", headers={"x-apisports-key": api_key})
            response.raise_for_status()
            requests = (response.json().get("response") or {}).get("requests") or {}
            used = int(requests.get("current") or 0)
            limit = int(requests.get("limit_day") or 100)
            return {
                "used": used,
                "limit": limit,
                "remaining": max(0, limit - used),
                "status": "verified",
            }
        except Exception as error:
            # Fail closed: an unverified quota must never be treated as a fresh
            # daily allowance, otherwise a transient outage can exhaust a free
            # account or trigger provider throttling.
            return {
                "used": 0,
                "limit": 0,
                "remaining": 0,
                "status": "unavailable",
                "error": str(error)[:180],
            }


collector = IngestionCollector()
