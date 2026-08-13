from datetime import date, timedelta

import pytest

from app.research.model import (
    apply_temperature,
    blend_with_market,
    current_team_form,
    dixon_coles_probabilities,
    fit_hyperparameters,
    run_walk_forward,
)


def _match(
    match_id: int,
    match_date: date,
    home: str,
    away: str,
    home_goals: int,
    away_goals: int,
) -> dict:
    result = "H" if home_goals > away_goals else "D" if home_goals == away_goals else "A"
    return {
        "hist_match_id": match_id,
        "competition_code": "PL",
        "match_date": match_date,
        "home_team": home,
        "away_team": away,
        "home_team_norm": home.lower(),
        "away_team_norm": away.lower(),
        "home_goals": home_goals,
        "away_goals": away_goals,
        "full_time_result": result,
        "closing_odd_home": 2.5,
        "closing_odd_draw": 3.2,
        "closing_odd_away": 3.0,
        "opening_odd_home": 2.4,
        "opening_odd_draw": 3.3,
        "opening_odd_away": 3.1,
    }


def _training_matches() -> list[dict]:
    start = date(2024, 1, 1)
    teams = ("A", "B", "C", "D")
    matches = []
    for index in range(120):
        home = teams[index % 4]
        away = teams[(index + 1) % 4]
        matches.append(
            _match(
                index + 1,
                start + timedelta(days=index),
                home,
                away,
                (index + 1) % 3,
                index % 2,
            )
        )
    return matches


def test_dixon_coles_probabilities_are_normalized() -> None:
    probabilities = dixon_coles_probabilities(1.62, 1.08)
    assert all(0 <= value <= 1 for value in probabilities)
    assert abs(sum(probabilities) - 1) < 1e-10


def test_same_date_results_are_not_visible_to_each_other() -> None:
    test_date = date(2025, 1, 10)
    matches = _training_matches()
    matches.extend(
        [
            _match(1001, test_date, "A", "C", 12, 0),
            _match(1002, test_date, "B", "D", 0, 0),
        ]
    )
    original = run_walk_forward(
        matches,
        test_start_on=test_date,
        test_end_on=test_date,
        minimum_effective_matches=1,
    )
    matches[-2] = _match(1001, test_date, "A", "C", 0, 12)
    mutated = run_walk_forward(
        matches,
        test_start_on=test_date,
        test_end_on=test_date,
        minimum_effective_matches=1,
    )
    assert original[1]["probabilities"] == mutated[1]["probabilities"]


def test_future_result_change_cannot_change_earlier_prediction() -> None:
    first_test_date = date(2025, 1, 10)
    matches = _training_matches()
    matches.extend(
        [
            _match(1001, first_test_date, "A", "C", 1, 0),
            _match(1002, first_test_date + timedelta(days=7), "A", "D", 8, 0),
        ]
    )
    original = run_walk_forward(
        matches,
        test_start_on=first_test_date,
        test_end_on=first_test_date + timedelta(days=7),
        minimum_effective_matches=1,
    )
    matches[-1] = _match(1002, first_test_date + timedelta(days=7), "A", "D", 0, 8)
    mutated = run_walk_forward(
        matches,
        test_start_on=first_test_date,
        test_end_on=first_test_date + timedelta(days=7),
        minimum_effective_matches=1,
    )
    assert original[0]["probabilities"] == mutated[0]["probabilities"]
    assert original[0]["training_cutoff_on"] < original[0]["match_date"]
    assert original[0]["source_max_match_date"] < original[0]["match_date"]


def test_temperature_preserves_normalization_and_order() -> None:
    probabilities = (0.5, 0.3, 0.2)
    for temperature in (0.8, 1.0, 1.25):
        tempered = apply_temperature(probabilities, temperature)
        assert abs(sum(tempered) - 1) < 1e-10
        assert tempered[0] > tempered[1] > tempered[2]


def test_shot_blend_falls_back_to_goals_when_stats_missing() -> None:
    test_date = date(2025, 1, 10)
    matches = _training_matches()
    matches.append(_match(1001, test_date, "A", "C", 1, 0))
    goals_only = run_walk_forward(
        matches,
        test_start_on=test_date,
        test_end_on=test_date,
        shot_alpha=1.0,
        minimum_effective_matches=1,
    )
    blended_without_stats = run_walk_forward(
        matches,
        test_start_on=test_date,
        test_end_on=test_date,
        shot_alpha=0.5,
        minimum_effective_matches=1,
    )
    assert goals_only[0]["probabilities"] == blended_without_stats[0]["probabilities"]


def test_shot_blend_changes_predictions_when_stats_present() -> None:
    test_date = date(2025, 1, 10)
    matches = _training_matches()
    for index, match in enumerate(matches):
        match["home_shots"] = 10.0 + (index % 5)
        match["away_shots"] = 8.0 + (index % 3)
        match["home_shots_on_target"] = 4.0 + (index % 3)
        match["away_shots_on_target"] = 3.0 + (index % 2)
    matches.append(_match(1001, test_date, "A", "C", 1, 0))
    goals_only = run_walk_forward(
        matches,
        test_start_on=test_date,
        test_end_on=test_date,
        shot_alpha=1.0,
        minimum_effective_matches=1,
    )
    blended = run_walk_forward(
        matches,
        test_start_on=test_date,
        test_end_on=test_date,
        shot_alpha=0.5,
        minimum_effective_matches=1,
    )
    assert goals_only[0]["probabilities"] != blended[0]["probabilities"]
    assert abs(sum(blended[0]["probabilities"]) - 1) < 1e-10


def test_same_date_shot_stats_do_not_leak_into_predictions() -> None:
    test_date = date(2025, 1, 10)
    matches = _training_matches()
    for match in matches:
        match["home_shots"] = 12.0
        match["away_shots"] = 9.0
        match["home_shots_on_target"] = 5.0
        match["away_shots_on_target"] = 3.0
    first = _match(1001, test_date, "A", "C", 2, 0)
    second = _match(1002, test_date, "B", "D", 1, 1)
    first["home_shots"] = 30.0
    first["home_shots_on_target"] = 20.0
    first["away_shots"] = 1.0
    first["away_shots_on_target"] = 0.0
    matches.extend([first, second])
    original = run_walk_forward(
        matches,
        test_start_on=test_date,
        test_end_on=test_date,
        shot_alpha=0.5,
        minimum_effective_matches=1,
    )
    mutated_first = dict(first)
    mutated_first["home_shots"] = 2.0
    mutated_first["home_shots_on_target"] = 1.0
    matches[-2] = mutated_first
    mutated = run_walk_forward(
        matches,
        test_start_on=test_date,
        test_end_on=test_date,
        shot_alpha=0.5,
        minimum_effective_matches=1,
    )
    assert original[1]["probabilities"] == mutated[1]["probabilities"]


def test_blend_with_market_endpoints_and_normalization() -> None:
    model = (0.5, 0.3, 0.2)
    anchor = (0.35, 0.3, 0.35)
    assert blend_with_market(model, anchor, 0.0) == model
    assert blend_with_market(model, anchor, 1.0) == anchor
    assert blend_with_market(model, None, 0.7) == model
    blended = blend_with_market(model, anchor, 0.7)
    assert abs(sum(blended) - 1) < 1e-10
    assert anchor[0] < blended[0] < model[0]


def test_walk_forward_uses_opening_anchor_only_when_weighted() -> None:
    test_date = date(2025, 1, 10)
    matches = _training_matches()
    matches.append(_match(1001, test_date, "A", "C", 1, 0))
    unanchored = run_walk_forward(
        matches,
        test_start_on=test_date,
        test_end_on=test_date,
        minimum_effective_matches=1,
    )
    anchored = run_walk_forward(
        matches,
        test_start_on=test_date,
        test_end_on=test_date,
        market_anchor_weight=0.85,
        minimum_effective_matches=1,
    )
    assert unanchored[0]["probabilities"] == unanchored[0]["model_probabilities"]
    assert anchored[0]["probabilities"] != anchored[0]["model_probabilities"]
    assert anchored[0]["model_probabilities"] == unanchored[0]["model_probabilities"]
    assert abs(sum(anchored[0]["probabilities"]) - 1) < 1e-10


def test_walk_forward_missing_opening_odds_falls_back_to_model() -> None:
    test_date = date(2025, 1, 10)
    matches = _training_matches()
    target = _match(1001, test_date, "A", "C", 1, 0)
    target["opening_odd_home"] = None
    target["opening_odd_draw"] = None
    target["opening_odd_away"] = None
    matches.append(target)
    anchored = run_walk_forward(
        matches,
        test_start_on=test_date,
        test_end_on=test_date,
        market_anchor_weight=0.85,
        minimum_effective_matches=1,
    )
    assert anchored[0]["market_anchor_probabilities"] is None
    assert anchored[0]["probabilities"] == anchored[0]["model_probabilities"]


def test_fit_hyperparameters_selects_anchor_weight() -> None:
    matches = [m for m in _training_matches() if m["match_date"] <= date(2024, 4, 30)]
    fitted = fit_hyperparameters(
        matches,
        validation_start_on=date(2024, 3, 1),
        validation_end_on=date(2024, 4, 30),
        alpha_grid=(1.0,),
        rho_grid=(-0.08,),
        temperature_grid=(1.0,),
        anchor_weight_grid=(0.0, 0.5, 1.0),
        minimum_effective_matches=1,
    )
    assert fitted["market_anchor_weight"] in (0.0, 0.5, 1.0)
    assert fitted["validation_anchored_matches"] == fitted["validation_matches"]


def test_fit_hyperparameters_rejects_post_validation_matches() -> None:
    matches = _training_matches()
    with pytest.raises(ValueError):
        fit_hyperparameters(
            matches,
            validation_start_on=date(2024, 3, 1),
            validation_end_on=date(2024, 3, 31),
            alpha_grid=(1.0,),
            rho_grid=(-0.08,),
            temperature_grid=(1.0,),
            minimum_effective_matches=1,
        )


def test_fit_hyperparameters_is_deterministic() -> None:
    matches = [m for m in _training_matches() if m["match_date"] <= date(2024, 4, 30)]
    kwargs = dict(
        validation_start_on=date(2024, 3, 1),
        validation_end_on=date(2024, 4, 30),
        alpha_grid=(1.0, 0.5),
        rho_grid=(-0.12, -0.08, 0.0),
        temperature_grid=(0.9, 1.0, 1.1),
        minimum_effective_matches=1,
    )
    first = fit_hyperparameters(matches, **kwargs)
    second = fit_hyperparameters(matches, **kwargs)
    assert first == second
    assert first["validation_matches"] > 0


def test_team_form_is_computed_from_real_rows() -> None:
    rows = [
        _match(1, date(2026, 1, 1), "A", "B", 2, 0),
        _match(2, date(2026, 1, 8), "B", "A", 1, 1),
    ]
    result = current_team_form(rows, {"a", "b"}, window=20)
    team_a = next(item for item in result if item["team_key"] == "a")
    assert team_a["matches"] == 2
    assert team_a["wins"] == 1
    assert team_a["draws"] == 1
    assert team_a["points_per_match"] == 2.0
