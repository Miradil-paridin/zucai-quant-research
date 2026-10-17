from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .database import connect
from .data_sources import API_FOOTBALL_URL, FOOTBALL_DATA_URL, normalize_api_football, normalize_football_data, supported_football_data
from .localization import is_supported_competition
from .repository import Repository, repository


class CollectorBusyError(RuntimeError):
    pass


class IngestionCollector:
    def __init__(self, data_repository: Repository = repository) -> None:
        self.repository = data_repository
        self._lock = asyncio.Lock()
        self._api_football_request_lock = asyncio.Lock()
        self._api_football_last_request_at: float | None = None

    @property
    def running(self) -> bool:
        return self._lock.locked()

    async def run(self, *, days: int = 7) -> dict[str, Any]:
        if self._lock.locked():
            raise CollectorBusyError("an ingestion run is already in progress")
        async with self._lock:
            return await self._run(days=max(1, min(days, 7)))

    async def _run(self, *, days: int) -> dict[str, Any]:
        # Fail closed before touching provider quotas when durable storage is down.
        with connect() as connection:
            connection.execute("SELECT 1")
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
            per_run_limit = max(1, int(os.getenv("API_FOOTBALL_MAX_REQUESTS_PER_RUN", "9")))
            # The quota probe is itself an API-Football request. Keep the whole
            # run below the free plan's ten-requests-per-minute ceiling.
            run_request_budget = min(
                int(quota.get("remaining", 0)),
                max(0, per_run_limit - int(api_key is not None)),
            )

            api_fixtures: list[dict[str, Any]] = []
            if api_key and run_request_budget > 0:
                batch_id = self.repository.start_batch("api-football", "fixtures")
                try:
                    response, attempts = await self._api_football_get(
                        client,
                        api_key,
                        "fixtures",
                        params={"date": local_today.isoformat(), "timezone": "UTC"},
                    )
                    summary["api_football_requests"] += attempts
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
                        params={"dateFrom": (local_today - timedelta(days=3)).isoformat(), "dateTo": end_date.isoformat()},
                        headers={"X-Auth-Token": football_data_key},
                    )
                    summary["football_data_requests"] += 1
                    response.raise_for_status()
                    payload = response.json()
                    football_data_fixtures = [
                        normalize_football_data(item)
                        for item in payload.get("matches", [])
                        if supported_football_data(item)
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
            scheduled_fixture_ids = [
                str(item["provider_ids"]["api-football"])
                for item in api_fixtures
                if item["status"] == "scheduled"
            ]
            remaining = run_request_budget - summary["api_football_requests"]
            stale_id_loader = getattr(self.repository, "stale_fixture_ids", None)
            if api_key and remaining >= 1 and stale_id_loader is not None:
                stale_ids = stale_id_loader(
                    "api-football",
                    limit=min(3, remaining),
                )
                if stale_ids:
                    await self._refresh_stale_fixtures(
                        client,
                        api_key,
                        stale_ids,
                        summary,
                    )
            remaining = run_request_budget - summary["api_football_requests"]
            if scheduled_fixture_ids and remaining >= 1 and self.repository.resource_due(
                "api-football", "odds", minimum_age=timedelta(hours=6)
            ):
                await self._collect_targeted_odds(
                    client,
                    api_key,
                    scheduled_fixture_ids,
                    summary,
                    max_requests=remaining,
                )
            remaining = run_request_budget - summary["api_football_requests"]
            if fixture_ids and remaining >= 1 and self.repository.resource_due(
                "api-football", "injuries", minimum_age=timedelta(hours=12)
            ):
                await self._collect_api_resource(
                    client, api_key, "injuries", {"date": local_today.isoformat()}, summary,
                    lambda batch_id, payload, captured: self.repository.persist_injuries(
                        batch_id=batch_id, payload=payload, captured_at=captured
                    ),
                )

            detail_ids = []
            now = datetime.now(UTC)
            for fixture in api_fixtures:
                kickoff = datetime.fromisoformat(fixture["kickoff_utc"].replace("Z", "+00:00"))
                if fixture["status"] != "scheduled" or kickoff <= now + timedelta(minutes=90):
                    detail_ids.append(str(fixture["provider_ids"]["api-football"]))
            remaining = run_request_budget - summary["api_football_requests"]
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
            response, attempts = await self._api_football_get(
                client,
                api_key,
                endpoint or resource_name,
                params=params,
            )
            summary["api_football_requests"] += attempts
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

    async def _collect_targeted_odds(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        fixture_ids: list[str],
        summary: dict[str, Any],
        *,
        max_requests: int,
    ) -> None:
        batch_id = self.repository.start_batch("api-football", "odds")
        requests_made = 0
        result_count = 0
        aggregate = {"raw_inserted": 0, "odds_inserted": 0}
        found: set[str] = set()
        checked: set[str] = set()
        truncated: set[str] = set()
        page_cap = max(1, min(3, int(os.getenv("API_FOOTBALL_FREE_ODDS_PAGE_CAP", "3"))))
        try:
            for fixture_id in fixture_ids:
                if requests_made >= max_requests:
                    break
                checked.add(fixture_id)
                total_pages = 1
                page = 1
                while page <= min(total_pages, page_cap) and requests_made < max_requests:
                    response, attempts = await self._api_football_get(
                        client,
                        api_key,
                        "odds",
                        params={"fixture": fixture_id, "bet": 1, "page": page},
                    )
                    requests_made += attempts
                    summary["api_football_requests"] += attempts
                    response.raise_for_status()
                    payload = response.json()
                    if payload.get("errors"):
                        raise RuntimeError(str(payload["errors"]))
                    total_pages = int((payload.get("paging") or {}).get("total") or 1)
                    if total_pages > page_cap:
                        truncated.add(fixture_id)
                    result_count += int(payload.get("results") or 0)
                    response_items = payload.get("response") or []
                    if response_items:
                        found.add(fixture_id)
                    persisted = self.repository.persist_odds(
                        batch_id=batch_id, payload=payload, captured_at=datetime.now(UTC)
                    )
                    aggregate["raw_inserted"] += persisted["raw_inserted"]
                    aggregate["odds_inserted"] += persisted["odds_inserted"]
                    page += 1
            status = "succeeded" if len(checked) == len(fixture_ids) and not truncated else "partial"
            self.repository.finish_batch(
                batch_id, request_count=requests_made, record_count=result_count, status=status
            )
            summary["resources"]["odds"] = {
                **aggregate,
                "requests_made": requests_made,
                "fixtures_checked": len(checked),
                "target_fixtures_found": len(found),
                "target_fixtures": len(fixture_ids),
                "page_cap": page_cap,
                "page_cap_truncated_fixtures": len(truncated),
            }
        except Exception as error:
            if requests_made:
                self.repository.finish_batch(
                    batch_id, request_count=requests_made, record_count=result_count, status="partial"
                )
            else:
                self.repository.fail_batch(batch_id, error, request_count=requests_made)
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
                response, attempts = await self._api_football_get(
                    client,
                    api_key,
                    "fixtures",
                    params={"id": fixture_id},
                )
                summary["api_football_requests"] += attempts
                requests_made += attempts
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

    async def _refresh_stale_fixtures(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        fixture_ids: list[str],
        summary: dict[str, Any],
    ) -> None:
        batch_id = self.repository.start_batch("api-football", "fixtures_reconciliation")
        requests_made = records = upserted = 0
        try:
            for fixture_id in fixture_ids:
                response, attempts = await self._api_football_get(
                    client,
                    api_key,
                    "fixtures",
                    params={"id": fixture_id},
                )
                requests_made += attempts
                summary["api_football_requests"] += attempts
                response.raise_for_status()
                payload = response.json()
                if payload.get("errors"):
                    raise RuntimeError(str(payload["errors"]))
                fixtures = [
                    normalize_api_football(item)
                    for item in payload.get("response") or []
                ]
                records += len(fixtures)
                persisted = self.repository.persist_fixture_response(
                    batch_id=batch_id,
                    source="api-football",
                    payload=payload,
                    fixtures=fixtures,
                    source_entity_id=fixture_id,
                )
                upserted += persisted["fixtures_upserted"]
            self.repository.finish_batch(
                batch_id,
                request_count=requests_made,
                record_count=records,
                status="succeeded",
            )
            summary["resources"]["fixtures_reconciliation"] = {
                "requested": len(fixture_ids),
                "returned": records,
                "fixtures_upserted": upserted,
            }
        except Exception as error:
            if requests_made:
                self.repository.finish_batch(
                    batch_id,
                    request_count=requests_made,
                    record_count=records,
                    status="partial",
                )
            else:
                self.repository.fail_batch(batch_id, error)
            summary["errors"].append(
                {"resource": "fixtures_reconciliation", "message": str(error)[:300]}
            )

    async def _api_football_quota(self, client: httpx.AsyncClient, api_key: str | None) -> dict[str, Any]:
        if not api_key:
            return {"used": 0, "limit": 0, "remaining": 0, "status": "not_configured"}
        try:
            response, _ = await self._api_football_get(client, api_key, "status")
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

    async def _api_football_get(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> tuple[httpx.Response, int]:
        min_interval = max(0.0, float(os.getenv("API_FOOTBALL_MIN_INTERVAL_SECONDS", "6.5")))
        async with self._api_football_request_lock:
            loop = asyncio.get_running_loop()
            if self._api_football_last_request_at is not None:
                wait_for = min_interval - (loop.time() - self._api_football_last_request_at)
                if wait_for > 0:
                    await asyncio.sleep(wait_for)
            response = await client.get(
                f"{API_FOOTBALL_URL}/{endpoint}",
                params=params,
                headers={"x-apisports-key": api_key},
            )
            self._api_football_last_request_at = loop.time()
            attempts = 1
            if response.status_code == 429:
                retry_after_raw = response.headers.get("Retry-After")
                try:
                    retry_after = float(retry_after_raw) if retry_after_raw else 60.0
                except ValueError:
                    retry_after = 60.0
                await asyncio.sleep(max(min_interval, retry_after))
                response = await client.get(
                    f"{API_FOOTBALL_URL}/{endpoint}",
                    params=params,
                    headers={"x-apisports-key": api_key},
                )
                self._api_football_last_request_at = loop.time()
                attempts += 1
            return response, attempts


collector = IngestionCollector()
