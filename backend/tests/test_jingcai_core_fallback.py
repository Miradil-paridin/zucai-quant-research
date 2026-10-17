from datetime import UTC, date, datetime

from app.jingcai.core_fallback import normalize_jingcai_fixture


def test_jingcai_fixture_is_explicitly_schedule_only_fallback() -> None:
    item = normalize_jingcai_fixture(
        {
            "official_match_id": "35602",
            "league_code": "EFL",
            "league_name": "英格兰冠军联赛",
            "home_team": "米尔沃尔",
            "away_team": "雷克斯汉姆",
            "match_date_local": date(2026, 9, 3),
            "match_time_local": "02:45:00",
        }
    )
    assert item is not None
    assert item["kickoff_utc"] == datetime(2026, 9, 2, 18, 45, tzinfo=UTC).isoformat()
    assert item["provider_ids"] == {"sporttery": "35602"}
    assert item["coverage"] == "selected_for_sale"
    assert item["status"] == "scheduled"
    assert item["home_team"]["name_original"] == "Millwall"
