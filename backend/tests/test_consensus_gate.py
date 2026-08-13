from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.jingcai import consensus_gate
from app.jingcai.consensus_gate import evaluate_gate, finalize_stale_collection_runs


NOW = datetime(2026, 8, 13, 4, 0, tzinfo=UTC)


def match_row(league: str, sell_end: str) -> dict[str, object]:
    return {
        "league_name": league,
        "match_date_local": None,
        "match_time_local": None,
        "sell_end_local": sell_end,
    }


def decision(**overrides):
    values = {
        "now_utc": NOW,
        "last_success": NOW - timedelta(hours=24),
        "latest_started": NOW - timedelta(hours=24),
        "latest_finished": NOW - timedelta(hours=24),
        "latest_status": "succeeded",
        "monthly_requests": 100,
        "quota_remaining": 300,
        "match_rows": [match_row("英格兰超级联赛", "2026-08-13 14:00")],
    }
    values.update(overrides)
    return evaluate_gate(**values)


def test_kickoff_window_queries_only_sports_with_an_upcoming_match() -> None:
    result = decision(
        match_rows=[
            match_row("英格兰超级联赛", "2026-08-13 14:00"),
            match_row("西班牙甲级联赛", "2026-08-14 14:00"),
        ]
    )
    assert result.proceed is True
    assert result.reason == "kickoff_window"
    assert result.sport_keys == ("soccer_epl",)


def test_recent_success_blocks_repeat_even_when_prices_did_not_change() -> None:
    result = decision(last_success=NOW - timedelta(hours=1))
    assert result.proceed is False
    assert result.reason.startswith("recent_success")


def test_heartbeat_queries_all_active_mapped_sports() -> None:
    result = decision(
        match_rows=[
            match_row("英格兰超级联赛", "2026-08-14 14:00"),
            match_row("西班牙甲级联赛", "2026-08-14 16:00"),
        ]
    )
    assert result.reason == "daily_heartbeat"
    assert result.sport_keys == ("soccer_epl", "soccer_spain_la_liga")


def test_monthly_budget_is_a_hard_stop() -> None:
    result = decision(monthly_requests=400)
    assert result.proceed is False
    assert result.reason == "monthly_budget_exhausted"


def test_zero_monthly_budget_disables_collection() -> None:
    result = decision(monthly_requests=0, monthly_budget=0)
    assert result.proceed is False
    assert result.reason == "monthly_budget_exhausted"


def test_provider_reserve_is_a_hard_stop() -> None:
    result = decision(quota_remaining=100)
    assert result.proceed is False
    assert result.reason == "provider_quota_reserve"


def test_unverified_provider_quota_blocks_the_rest_of_the_month() -> None:
    result = decision(quota_remaining=250, quota_verified=False)
    assert result.proceed is False
    assert result.reason == "provider_quota_unknown"


def test_new_month_without_a_current_quota_observation_can_probe_again() -> None:
    result = decision(
        monthly_requests=0,
        quota_remaining=None,
        last_success=NOW - timedelta(days=2),
    )
    assert result.proceed is True
    assert result.reason in {"kickoff_window", "daily_heartbeat"}


def test_failed_run_has_retry_backoff() -> None:
    result = decision(
        latest_status="failed",
        latest_finished=NOW - timedelta(hours=1),
        last_success=NOW - timedelta(days=2),
    )
    assert result.proceed is False
    assert result.reason.startswith("failure_backoff")


def test_recent_incomplete_run_has_backoff_but_stale_run_can_recover() -> None:
    recent = decision(
        latest_status="running",
        latest_started=NOW - timedelta(hours=1),
        latest_finished=None,
        last_success=NOW - timedelta(days=2),
    )
    assert recent.proceed is False
    assert recent.reason.startswith("previous_run_incomplete")

    stale = decision(
        latest_status="running",
        latest_started=NOW - timedelta(hours=4),
        latest_finished=None,
        last_success=NOW - timedelta(days=2),
    )
    assert stale.proceed is True


def test_unmapped_leagues_are_reported_without_requesting() -> None:
    result = decision(match_rows=[match_row("未知联赛", "2026-08-13 14:00")])
    assert result.proceed is False
    assert result.reason == "no_mapped_sports"
    assert result.unmapped_leagues == ("未知联赛",)


def test_stale_running_batches_are_finalized_before_recovery(monkeypatch) -> None:
    class Cursor:
        rowcount = 2

    class Connection:
        params = None

        def execute(self, _sql, params):
            self.params = params
            return Cursor()

    connection = Connection()
    monkeypatch.delenv("ODDS_API_FAILURE_RETRY_HOURS", raising=False)

    count = finalize_stale_collection_runs(connection, now_utc=NOW)

    assert count == 2
    assert connection.params[-1] == NOW - timedelta(hours=3)


def test_invalid_budget_environment_value_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("ODDS_API_MONTHLY_REQUEST_BUDGET", "40O")
    with pytest.raises(RuntimeError, match="must be an integer"):
        consensus_gate._env_int("ODDS_API_MONTHLY_REQUEST_BUDGET", 400)


@pytest.mark.parametrize("value", ["soon", "-1", "nan"])
def test_invalid_interval_environment_value_fails_closed(monkeypatch, value) -> None:
    monkeypatch.setenv("ODDS_API_FAILURE_RETRY_HOURS", value)
    with pytest.raises(RuntimeError, match="must be"):
        consensus_gate._env_float("ODDS_API_FAILURE_RETRY_HOURS", 3.0)
