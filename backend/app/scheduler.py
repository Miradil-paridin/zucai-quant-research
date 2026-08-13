from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .collector import collector


scheduler = AsyncIOScheduler(timezone="UTC")


def start_scheduler() -> None:
    if os.getenv("INGESTION_SCHEDULER_ENABLED", "true").lower() not in {"1", "true", "yes", "on"}:
        return
    if scheduler.running:
        return
    interval_hours = max(1, int(os.getenv("INGESTION_INTERVAL_HOURS", "3")))
    scheduler.add_job(
        collector.run,
        "interval",
        hours=interval_hours,
        kwargs={"days": 7},
        id="football-data-ingestion",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        next_run_time=datetime.now(UTC) + timedelta(hours=interval_hours),
    )
    scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


def scheduler_status() -> dict[str, object]:
    job = scheduler.get_job("football-data-ingestion") if scheduler.running else None
    return {
        "enabled": scheduler.running,
        "next_run_time": job.next_run_time if job else None,
        "collector_running": collector.running,
    }
