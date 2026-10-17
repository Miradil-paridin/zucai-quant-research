"""Quota-aware gate for international consensus odds collection.

The Windows task may wake hourly, but this module decides whether an external
request is useful and affordable. Decisions are based on completed collection
runs, not changed price rows, so an unchanged or empty response still advances
the successful-collection clock.
"""

from __future__ import annotations

import os
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Iterable

from psycopg.types.json import Jsonb

from .consensus import PROVIDER, RESOURCE_TYPE, SPORT_KEY_BY_LEAGUE


BEIJING = timezone(timedelta(hours=8))
KICKOFF_LOOKAHEAD_HOURS = 5.0
HEARTBEAT_HOURS = 23.0
LOCK_NAME = "zucai-consensus-odds"


@dataclass(frozen=True)
class GateDecision:
    proceed: bool
    reason: str
    sport_keys: tuple[str, ...] = ()
    unmapped_leagues: tuple[str, ...] = ()
    monthly_requests: int = 0
    quota_remaining: int | None = None
    quota_verified: bool | None = None


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}")
    return value


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a number") from error
    if not math.isfinite(value) or value < minimum:
        raise RuntimeError(f"{name} must be a finite number at least {minimum}")
    return value


def _parse_beijing(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=BEIJING)
        except ValueError:
            continue
    return None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _row_moment(row: dict[str, Any]) -> datetime | None:
    moment = _parse_beijing(row.get("sell_end_local"))
    if moment is None and row.get("match_date_local") and row.get("match_time_local"):
        moment = _parse_beijing(f"{row['match_date_local']} {row['match_time_local']}")
    return moment.astimezone(UTC) if moment else None


def _mapped_sports(
    rows: Iterable[dict[str, Any]],
    *,
    now_utc: datetime,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    active: list[str] = []
    upcoming: list[str] = []
    unmapped: set[str] = set()
    horizon = now_utc + timedelta(hours=KICKOFF_LOOKAHEAD_HOURS)
    for row in rows:
        league = str(row.get("league_name") or "")
        sport_key = SPORT_KEY_BY_LEAGUE.get(league)
        if not sport_key:
            if league:
                unmapped.add(league)
            continue
        if sport_key not in active:
            active.append(sport_key)
        moment = _row_moment(row)
        if moment and now_utc <= moment <= horizon and sport_key not in upcoming:
            upcoming.append(sport_key)
    return tuple(active), tuple(upcoming), tuple(sorted(unmapped))


def evaluate_gate(
    *,
    now_utc: datetime,
    last_success: datetime | None,
    latest_started: datetime | None,
    latest_finished: datetime | None,
    latest_status: str | None,
    monthly_requests: int,
    quota_remaining: int | None,
    match_rows: Iterable[dict[str, Any]],
    quota_verified: bool | None = None,
    monthly_budget: int = 400,
    quota_reserve: int = 100,
    kickoff_min_gap_hours: float = 3.0,
    heartbeat_hours: float = HEARTBEAT_HOURS,
    failure_retry_hours: float = 3.0,
    reference_keys: tuple[str, ...] = (),
) -> GateDecision:
    now = _as_utc(now_utc) or datetime.now(UTC)
    last_ok = _as_utc(last_success)
    last_started = _as_utc(latest_started)
    last_finished = _as_utc(latest_finished)
    active_keys, upcoming_keys, unmapped = _mapped_sports(match_rows, now_utc=now)

    base = {
        "unmapped_leagues": unmapped,
        "monthly_requests": max(0, monthly_requests),
        "quota_remaining": quota_remaining,
        "quota_verified": quota_verified,
    }
    if monthly_requests >= monthly_budget:
        return GateDecision(False, "monthly_budget_exhausted", **base)
    if quota_remaining is not None and quota_remaining <= quota_reserve:
        return GateDecision(False, "provider_quota_reserve", **base)
    if quota_verified is False:
        return GateDecision(False, "provider_quota_unknown", **base)
    if latest_status == "running" and last_started:
        running_age = (now - last_started).total_seconds() / 3600.0
        if running_age < failure_retry_hours:
            return GateDecision(False, f"previous_run_incomplete({running_age:.1f}h)", **base)
    if latest_status == "failed" and last_finished:
        failure_age = (now - last_finished).total_seconds() / 3600.0
        if failure_age < failure_retry_hours:
            return GateDecision(False, f"failure_backoff({failure_age:.1f}h)", **base)
    if reference_keys:
        return GateDecision(True, "reference_checkpoint", reference_keys, **base)
    if not active_keys:
        return GateDecision(False, "no_mapped_sports", **base)

    hours_since_success = (
        1e9 if last_ok is None else max(0.0, (now - last_ok).total_seconds() / 3600.0)
    )
    if upcoming_keys and hours_since_success >= kickoff_min_gap_hours:
        return GateDecision(True, "kickoff_window", upcoming_keys, **base)
    if hours_since_success >= heartbeat_hours:
        return GateDecision(True, "daily_heartbeat", active_keys, **base)
    return GateDecision(
        False,
        f"recent_success({hours_since_success:.1f}h,upcoming={bool(upcoming_keys)})",
        **base,
    )


def should_collect_now(connection: Any, *, now_utc: datetime | None = None) -> GateDecision:
    stats = connection.execute(
        """
        SELECT
          (
            SELECT max(finished_at_utc)
            FROM raw.ingestion_batches
            WHERE source_name = %s AND resource_type = %s
              AND status IN ('succeeded', 'partial')
              AND CASE
                    WHEN report->>'sports_succeeded' ~ '^\\d{1,9}$'
                      THEN (report->>'sports_succeeded')::integer
                    ELSE 0
                  END > 0
          ) AS last_success,
          (
            SELECT started_at_utc
            FROM raw.ingestion_batches
            WHERE source_name = %s AND resource_type = %s
            ORDER BY started_at_utc DESC, batch_id DESC LIMIT 1
          ) AS latest_started,
          (
            SELECT finished_at_utc
            FROM raw.ingestion_batches
            WHERE source_name = %s AND resource_type = %s
            ORDER BY started_at_utc DESC, batch_id DESC LIMIT 1
          ) AS latest_finished,
          (
            SELECT status
            FROM raw.ingestion_batches
            WHERE source_name = %s AND resource_type = %s
            ORDER BY started_at_utc DESC, batch_id DESC LIMIT 1
          ) AS latest_status,
          (
            SELECT COALESCE(sum(request_count), 0)::integer
            FROM raw.ingestion_batches
            WHERE source_name = %s AND resource_type = %s
              AND started_at_utc >= date_trunc('month', now())
              AND started_at_utc < date_trunc('month', now()) + interval '1 month'
          ) AS monthly_requests,
          (
            SELECT CASE
              WHEN report->>'quota_remaining' ~ '^\\d{1,9}$'
                THEN (report->>'quota_remaining')::integer
              ELSE NULL
            END
            FROM raw.ingestion_batches
            WHERE source_name = %s AND resource_type = %s
              AND report ? 'quota_remaining'
              AND started_at_utc >= date_trunc('month', now())
              AND started_at_utc < date_trunc('month', now()) + interval '1 month'
            ORDER BY started_at_utc DESC, batch_id DESC LIMIT 1
          ) AS quota_remaining,
          (
            SELECT CASE
              WHEN report->>'quota_verified' = 'true' THEN true
              WHEN report->>'quota_verified' = 'false' THEN false
              ELSE NULL
            END
            FROM raw.ingestion_batches
            WHERE source_name = %s AND resource_type = %s
              AND report ? 'quota_verified'
              AND started_at_utc >= date_trunc('month', now())
              AND started_at_utc < date_trunc('month', now()) + interval '1 month'
            ORDER BY started_at_utc DESC, batch_id DESC LIMIT 1
          ) AS quota_verified
        """,
        (PROVIDER, RESOURCE_TYPE) * 7,
    ).fetchone()
    match_rows = connection.execute(
        """
        SELECT league_name, match_date_local, match_time_local, sell_end_local
        FROM jingcai.jc_matches
        WHERE last_seen_at_utc > now() - interval '3 days'
        """
    ).fetchall()
    reference_keys = reference_checkpoint_sports(connection, now_utc or datetime.now(UTC))
    return evaluate_gate(
        now_utc=now_utc or datetime.now(UTC),
        last_success=stats["last_success"],
        latest_started=stats["latest_started"],
        latest_finished=stats["latest_finished"],
        latest_status=stats["latest_status"],
        monthly_requests=int(stats["monthly_requests"] or 0),
        quota_remaining=stats["quota_remaining"],
        quota_verified=stats["quota_verified"],
        match_rows=match_rows,
        monthly_budget=_env_int("ODDS_API_MONTHLY_REQUEST_BUDGET", 400, minimum=0),
        quota_reserve=_env_int("ODDS_API_QUOTA_RESERVE", 100),
        kickoff_min_gap_hours=_env_float("ODDS_API_KICKOFF_MIN_GAP_HOURS", 3.0),
        failure_retry_hours=_env_float("ODDS_API_FAILURE_RETRY_HOURS", 3.0),
        reference_keys=reference_keys,
    )


def reference_checkpoint_sports(connection: Any, now: datetime) -> tuple[str, ...]:
    """Query just the leagues due for a prospective checkpoint, at most once
    per half hour per league. Quota and failure gates still apply afterward.
    """
    from ..reference.model import due_checkpoint
    rows=connection.execute(
        """SELECT c.name AS league_name,m.kickoff_utc FROM core.matches m
           JOIN core.seasons s USING(season_id) JOIN core.competitions c USING(competition_id)
           WHERE m.status='scheduled' AND m.kickoff_utc>%s AND m.kickoff_utc<=%s""",
        (now,now+timedelta(hours=24)),
    ).fetchall()
    keys=sorted({SPORT_KEY_BY_LEAGUE[r["league_name"]] for r in rows
                 if r["league_name"] in SPORT_KEY_BY_LEAGUE and due_checkpoint(r["kickoff_utc"],now)})
    return tuple(key for key in keys if not connection.execute(
        """SELECT 1 FROM raw.ingestion_batches WHERE source_name=%s AND resource_type=%s
           AND started_at_utc>=%s AND request_count>0 AND report->'sport_keys' ? %s LIMIT 1""",
        (PROVIDER,RESOURCE_TYPE,now-timedelta(minutes=30),key),
    ).fetchone())


def finalize_stale_collection_runs(
    connection: Any,
    *,
    now_utc: datetime | None = None,
) -> int:
    """Turn abandoned running batches into truthful terminal records."""

    current = _as_utc(now_utc) or datetime.now(UTC)
    retry_hours = _env_float("ODDS_API_FAILURE_RETRY_HOURS", 3.0)
    cutoff = current - timedelta(hours=retry_hours)
    cursor = connection.execute(
        """
        UPDATE raw.ingestion_batches
        SET finished_at_utc = started_at_utc,
            status = CASE
              WHEN CASE
                     WHEN report->>'sports_succeeded' ~ '^\\d{1,9}$'
                       THEN (report->>'sports_succeeded')::integer
                     ELSE 0
                   END > 0
                THEN 'partial'
              ELSE 'failed'
            END,
            error_summary = COALESCE(
              error_summary,
              'collector process ended before batch finalization'
            ),
            report = report || %s
        WHERE source_name = %s AND resource_type = %s
          AND status = 'running'
          AND started_at_utc <= %s
        """,
        (
            Jsonb(
                {
                    "recovered_stale_run": True,
                    "stop_reason": "collector_process_terminated",
                }
            ),
            PROVIDER,
            RESOURCE_TYPE,
            cutoff,
        ),
    )
    return max(0, int(cursor.rowcount or 0))


def acquire_collection_lock(connection: Any) -> bool:
    row = connection.execute(
        "SELECT pg_try_advisory_lock(hashtext(%s)) AS acquired", (LOCK_NAME,)
    ).fetchone()
    return bool(row and row["acquired"])


def release_collection_lock(connection: Any) -> None:
    connection.execute("SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_NAME,))
