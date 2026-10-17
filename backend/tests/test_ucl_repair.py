from datetime import UTC, date, datetime, timedelta
import json

from psycopg import OperationalError

from app.data_sources import normalize_football_data, supported_football_data
from app.jingcai.repricing_lag import evaluate_match
from scripts import analyze_repricing_lag as runner
from app import database
from psycopg.errors import DeadlockDetected


def test_v4_top_level_area_admits_ucl_and_domestic_leagues():
    fixture = {"id": 1, "area": {"name": "Europe"},
        "competition": {"code": "CL", "name": "UEFA Champions League"},
        "season": {"startDate": "2026-09-08", "endDate": "2027-01-27"},
        "homeTeam": {"id": 1, "name": "Real Madrid CF"},
        "awayTeam": {"id": 2, "name": "FC Internazionale Milano"},
        "utcDate": "2026-09-08T19:00:00Z", "status": "FINISHED",
        "score": {"fullTime": {"home": 2, "away": 1}}}
    assert supported_football_data(fixture)
    normalized = normalize_football_data(fixture)
    assert normalized["country_original"] == "Europe"
    assert normalized["season_label"] == "2026/27"
    assert normalized["score"] == {"home": 2, "away": 1}
    assert normalized["home_team"]["name"] == "皇家马德里"
    fixture.update(area={"name": "England"}, competition={"name": "Premier League"})
    assert supported_football_data(fixture)
    fixture["area"] = {"name": "South Africa"}
    assert not supported_football_data(fixture)


KICKOFF = datetime(2026, 9, 8, 19, tzinfo=UTC)


def sample_match(snaps):
    return {"match_num": "001", "league": "test", "date": "2026-09-09",
            "home": "A", "away": "B", "match_date_local": date(2026, 9, 9),
            "match_time_local": "03:00:00", "sell_end_local": "2026-09-09 02:00:00", "snaps": snaps}


def odds(hours_before, h=3.5):
    return {"h": h, "d": 3.5, "a": 3.5, "t": KICKOFF-timedelta(hours=hours_before), "kickoff": KICKOFF}


def test_future_and_after_sale_observations_cannot_create_signals():
    result = evaluate_match(sample_match([odds(.5), odds(-1)]), [odds(2)], KICKOFF+timedelta(days=1))
    assert result["pairs"] == 0
    result = evaluate_match(sample_match([odds(3)]), [odds(2)], KICKOFF+timedelta(days=1))
    assert result["pairs"] == 0


def test_after_kickoff_prices_never_become_closing_proxy():
    result = evaluate_match(sample_match([odds(3)]), [odds(4), odds(-1, h=1.01)], KICKOFF+timedelta(days=1))
    assert result["pairs"] == 1
    assert result["signals"][0]["clv_vs_final"] == .1667
    assert result["signals"][0]["closing_age_hours"] == 4


def test_old_closing_proxy_is_missing_not_profitable():
    result = evaluate_match(sample_match([odds(11)]), [odds(12)], KICKOFF+timedelta(days=1))
    assert result["signals"]
    assert all(s["clv_vs_final"] is None for s in result["signals"])
    assert result["signals"][0]["evaluation_status"] == "missing_fresh_closing_proxy"


def test_failed_weekly_run_preserves_report_then_recovers(monkeypatch, tmp_path):
    report = tmp_path / "latest.json"
    report.write_text('{"old":true}')
    monkeypatch.setattr(runner, "OUT_PATH", report)
    monkeypatch.setattr(runner, "HISTORY_PATH", tmp_path / "history.jsonl")
    monkeypatch.setattr(runner, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(runner, "analyze_repricing_lag", lambda: (_ for _ in ()).throw(OperationalError("offline")))
    assert runner.main() == 1
    assert json.loads(report.read_text()) == {"old": True}
    monkeypatch.setattr(runner, "analyze_repricing_lag", lambda: dict(matches_with_both_sides=2,
        aligned_snapshot_pairs=4, signals=0, clv_mean=None, clv_positive_share=None))
    assert runner.main() == 0
    assert json.loads(report.read_text())["matches_with_both_sides"] == 2
    assert json.loads((tmp_path / "state.json").read_text())["consecutive_db_failures"] == 0


def test_schema_initialization_retries_deadlock_on_new_connection(monkeypatch):
    attempts = []
    class Connection:
        def __enter__(self):
            attempts.append(self)
            return self
        def __exit__(self, *_):
            pass
        def execute(self, *_args, **_kwargs):
            if len(attempts) == 1:
                raise DeadlockDetected("collector lock")
    monkeypatch.setattr(database, "connect", lambda **_: Connection())
    monkeypatch.setattr(database.time, "sleep", lambda _: None)
    database.initialize_database()
    assert len(attempts) == 2
    assert attempts[0] is not attempts[1]
