from datetime import UTC, date, datetime

from app.jingcai.matching import choose_consensus_event, jingcai_kickoff_utc


KICKOFF = datetime(2026, 9, 2, 18, 45, tzinfo=UTC)


def event(event_id: str, home: str, away: str) -> dict:
    return {
        "provider_event_id": event_id,
        "home_team": home,
        "away_team": away,
        "commence_time_utc": KICKOFF,
    }


def test_jingcai_kickoff_is_converted_from_shanghai_time() -> None:
    assert jingcai_kickoff_utc(date(2026, 9, 3), "02:45:00") == KICKOFF


def test_exact_team_alias_wins_in_ambiguous_kickoff_group() -> None:
    selected = choose_consensus_event(
        [
            event("other", "Burnley", "Middlesbrough"),
            event("wanted", "Millwall", "Wrexham AFC"),
        ],
        home_team="米尔沃尔",
        away_team="雷克斯汉姆",
    )
    assert selected is not None
    assert selected.provider_event_id == "wanted"
    assert selected.method == "team_alias_pair"


def test_unique_league_kickoff_candidate_is_safe_fallback() -> None:
    selected = choose_consensus_event(
        [event("only", "Toulouse", "Lille")],
        home_team="未维护主队",
        away_team="未维护客队",
    )
    assert selected is not None
    assert selected.provider_event_id == "only"
    assert selected.method == "league_kickoff_unique"


def test_ambiguous_unmapped_kickoff_group_is_rejected() -> None:
    selected = choose_consensus_event(
        [event("one", "A", "B"), event("two", "C", "D")],
        home_team="未维护主队",
        away_team="未维护客队",
    )
    assert selected is None


def test_ucl_aliases_resolve_simultaneous_matches_and_porto_variant() -> None:
    options = [event("madrid", "Real Madrid", "Inter Milan"),
               event("porto", "Porto", "Manchester City")]
    assert choose_consensus_event(options, home_team="皇家马德里", away_team="国际米兰").provider_event_id == "madrid"
    assert choose_consensus_event(options, home_team="波尔图", away_team="曼彻斯特城").provider_event_id == "porto"


def test_known_team_conflict_cannot_fall_back_to_unique_kickoff() -> None:
    assert choose_consensus_event([event("wrong", "Liverpool", "Arsenal")],
                                  home_team="皇家马德里", away_team="国际米兰") is None
