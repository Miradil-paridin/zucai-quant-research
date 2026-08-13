from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from math import exp, factorial, log, sqrt
from typing import Any, Iterable


RESULT_INDEX = {"H": 0, "D": 1, "A": 2}

# Raw shot proxy: shots on target count fully, off-target shots count 0.2.
# The proxy is rescaled online to the league goal rate (see goals_obs/proxy_obs),
# so these weights only set the relative value of on- vs off-target shots.
_OFF_TARGET_WEIGHT = 0.2
# Minimum decayed proxy mass required before the shot proxy is trusted.
_MIN_PROXY_OBS = 25.0


@dataclass
class TeamGoalState:
    home_for: float = 0.0
    home_against: float = 0.0
    home_matches: float = 0.0
    away_for: float = 0.0
    away_against: float = 0.0
    away_matches: float = 0.0

    def decay(self, factor: float) -> None:
        self.home_for *= factor
        self.home_against *= factor
        self.home_matches *= factor
        self.away_for *= factor
        self.away_against *= factor
        self.away_matches *= factor


@dataclass
class CompetitionGoalState:
    half_life_days: float
    prior_team_matches: float
    rho: float
    shot_alpha: float = 1.0
    last_date: date | None = None
    source_max_match_date: date | None = None
    home_goals: float = 0.0
    away_goals: float = 0.0
    matches: float = 0.0
    goals_obs: float = 0.0
    proxy_obs: float = 0.0
    teams: dict[str, TeamGoalState] = field(default_factory=dict)

    def advance_to(self, match_date: date) -> None:
        if self.last_date is None:
            self.last_date = match_date
            return
        elapsed = (match_date - self.last_date).days
        if elapsed <= 0:
            return
        factor = exp(-log(2) * elapsed / self.half_life_days)
        self.home_goals *= factor
        self.away_goals *= factor
        self.matches *= factor
        self.goals_obs *= factor
        self.proxy_obs *= factor
        for team in self.teams.values():
            team.decay(factor)
        self.last_date = match_date

    def predict(self, home_team: str, away_team: str) -> tuple[float, float, tuple[float, float, float]]:
        league_home = (self.home_goals + 1.45 * 20) / (self.matches + 20)
        league_away = (self.away_goals + 1.15 * 20) / (self.matches + 20)
        home = self.teams.get(home_team, TeamGoalState())
        away = self.teams.get(away_team, TeamGoalState())
        prior = self.prior_team_matches

        home_scored = (home.home_for + league_home * prior) / (home.home_matches + prior)
        away_conceded = (away.away_against + league_home * prior) / (away.away_matches + prior)
        away_scored = (away.away_for + league_away * prior) / (away.away_matches + prior)
        home_conceded = (home.home_against + league_away * prior) / (home.home_matches + prior)

        expected_home = _clamp(home_scored * away_conceded / league_home, 0.15, 4.5)
        expected_away = _clamp(away_scored * home_conceded / league_away, 0.15, 4.5)
        probabilities = dixon_coles_probabilities(expected_home, expected_away, self.rho)
        return expected_home, expected_away, probabilities

    def _raw_proxy(self, shots: float | None, shots_on_target: float | None) -> float | None:
        if shots is None or shots_on_target is None:
            return None
        if shots < 0 or shots_on_target < 0 or shots < shots_on_target:
            return None
        return shots_on_target + _OFF_TARGET_WEIGHT * (shots - shots_on_target)

    def _adjusted_goals(
        self,
        goals: float,
        shots: float | None,
        shots_on_target: float | None,
    ) -> float:
        """Blend actual goals with a league-rescaled shot proxy.

        Falls back to actual goals whenever the shot columns are missing or the
        online proxy-to-goal ratio has not accumulated enough evidence yet, so
        seasons without statistics behave exactly like the goals-only model.
        """

        if self.shot_alpha >= 1.0:
            return float(goals)
        raw = self._raw_proxy(shots, shots_on_target)
        if raw is None or self.proxy_obs < _MIN_PROXY_OBS or self.goals_obs <= 0:
            return float(goals)
        proxy = _clamp(raw * self.goals_obs / self.proxy_obs, 0.0, 8.0)
        return self.shot_alpha * float(goals) + (1.0 - self.shot_alpha) * proxy

    def update(
        self,
        home_team: str,
        away_team: str,
        home_goals: int,
        away_goals: int,
        *,
        home_shots: float | None = None,
        away_shots: float | None = None,
        home_shots_on_target: float | None = None,
        away_shots_on_target: float | None = None,
    ) -> None:
        adjusted_home = self._adjusted_goals(home_goals, home_shots, home_shots_on_target)
        adjusted_away = self._adjusted_goals(away_goals, away_shots, away_shots_on_target)
        home = self.teams.setdefault(home_team, TeamGoalState())
        away = self.teams.setdefault(away_team, TeamGoalState())
        home.home_for += adjusted_home
        home.home_against += adjusted_away
        home.home_matches += 1
        away.away_for += adjusted_away
        away.away_against += adjusted_home
        away.away_matches += 1
        self.home_goals += adjusted_home
        self.away_goals += adjusted_away
        self.matches += 1
        raw_home = self._raw_proxy(home_shots, home_shots_on_target)
        raw_away = self._raw_proxy(away_shots, away_shots_on_target)
        if raw_home is not None and raw_away is not None:
            self.goals_obs += home_goals + away_goals
            self.proxy_obs += raw_home + raw_away


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _poisson_probability(goals: int, expected: float) -> float:
    return exp(-expected) * expected**goals / factorial(goals)


def dixon_coles_probabilities(
    expected_home: float,
    expected_away: float,
    rho: float = -0.08,
    max_goals: int = 10,
) -> tuple[float, float, float]:
    """Return a normalized 1X2 distribution with a low-score DC correction."""

    home = draw = away = 0.0
    total = 0.0
    for home_goals in range(max_goals + 1):
        for away_goals in range(max_goals + 1):
            probability = (
                _poisson_probability(home_goals, expected_home)
                * _poisson_probability(away_goals, expected_away)
                * _dc_tau(home_goals, away_goals, expected_home, expected_away, rho)
            )
            total += probability
            if home_goals > away_goals:
                home += probability
            elif home_goals == away_goals:
                draw += probability
            else:
                away += probability
    if total <= 0:
        return 1 / 3, 1 / 3, 1 / 3
    return home / total, draw / total, away / total


def _dc_tau(
    home_goals: int,
    away_goals: int,
    expected_home: float,
    expected_away: float,
    rho: float,
) -> float:
    if home_goals == 0 and away_goals == 0:
        return max(0.01, 1 - expected_home * expected_away * rho)
    if home_goals == 0 and away_goals == 1:
        return max(0.01, 1 + expected_home * rho)
    if home_goals == 1 and away_goals == 0:
        return max(0.01, 1 + expected_away * rho)
    if home_goals == 1 and away_goals == 1:
        return max(0.01, 1 - rho)
    return 1.0


def apply_temperature(
    probabilities: tuple[float, float, float],
    temperature: float,
) -> tuple[float, float, float]:
    """Sharpen (t < 1) or flatten (t > 1) a distribution deterministically."""

    if temperature == 1.0:
        return probabilities
    powered = tuple(max(1e-12, value) ** (1.0 / temperature) for value in probabilities)
    total = sum(powered)
    return tuple(value / total for value in powered)


def blend_with_market(
    model_probs: tuple[float, float, float],
    anchor_probs: tuple[float, float, float] | None,
    weight: float,
) -> tuple[float, float, float]:
    """Geometric (log-odds) blend of model probabilities with a market prior.

    ``weight`` is the market share: 0 returns the pure model, 1 returns the
    pure market prior. A missing anchor always falls back to the pure model,
    so matches without opening odds behave exactly like the unanchored model.
    """

    if anchor_probs is None or weight <= 0.0:
        return model_probs
    if weight >= 1.0:
        return anchor_probs
    blended = tuple(
        max(1e-12, model) ** (1.0 - weight) * max(1e-12, anchor) ** weight
        for model, anchor in zip(model_probs, anchor_probs)
    )
    total = sum(blended)
    return tuple(value / total for value in blended)


def market_probabilities(
    odd_home: float | None,
    odd_draw: float | None,
    odd_away: float | None,
) -> tuple[float, float, float] | None:
    if not odd_home or not odd_draw or not odd_away:
        return None
    inverse = (1 / odd_home, 1 / odd_draw, 1 / odd_away)
    total = sum(inverse)
    if total <= 0:
        return None
    return tuple(value / total for value in inverse)


def run_walk_forward(
    matches: list[dict[str, Any]],
    *,
    test_start_on: date,
    test_end_on: date,
    half_life_days: float = 365.0,
    prior_team_matches: float = 5.0,
    rho: float = -0.08,
    shot_alpha: float = 1.0,
    temperature: float = 1.0,
    market_anchor_weight: float = 0.0,
    minimum_effective_matches: float = 80.0,
) -> list[dict[str, Any]]:
    """Predict each date before updating state with any result from that date.

    When ``market_anchor_weight`` > 0, the tempered model distribution is
    geometrically blended with the de-vigged *opening* market prior. Opening
    odds exist before match day, unlike closing odds, which are only used as
    an ex-post benchmark and never enter the prediction.
    """

    predictions: list[dict[str, Any]] = []
    matches_by_competition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for match in matches:
        matches_by_competition[match["competition_code"]].append(match)

    for competition_code, competition_matches in matches_by_competition.items():
        state = CompetitionGoalState(
            half_life_days=half_life_days,
            prior_team_matches=prior_team_matches,
            rho=rho,
            shot_alpha=shot_alpha,
        )
        by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for match in competition_matches:
            by_date[match["match_date"]].append(match)

        for match_date in sorted(by_date):
            state.advance_to(match_date)
            date_matches = by_date[match_date]
            if test_start_on <= match_date <= test_end_on and state.matches >= minimum_effective_matches:
                for match in date_matches:
                    expected_home, expected_away, probabilities = state.predict(
                        match["home_team_norm"], match["away_team_norm"]
                    )
                    model_probs = apply_temperature(probabilities, temperature)
                    anchor = market_probabilities(
                        match.get("opening_odd_home"),
                        match.get("opening_odd_draw"),
                        match.get("opening_odd_away"),
                    )
                    final_probs = blend_with_market(model_probs, anchor, market_anchor_weight)
                    benchmark = market_probabilities(
                        match.get("closing_odd_home"),
                        match.get("closing_odd_draw"),
                        match.get("closing_odd_away"),
                    )
                    predictions.append(
                        {
                            "hist_match_id": match["hist_match_id"],
                            "competition_code": competition_code,
                            "match_date": match_date,
                            "training_cutoff_on": date.fromordinal(match_date.toordinal() - 1),
                            "source_max_match_date": state.source_max_match_date,
                            "probabilities": final_probs,
                            "model_probabilities": model_probs,
                            "market_anchor_probabilities": anchor,
                            "market_probabilities": benchmark,
                            "expected_home_goals": expected_home,
                            "expected_away_goals": expected_away,
                            "actual_result": match["full_time_result"],
                        }
                    )

            # Updating only after every match on the date has been predicted
            # prevents same-day ordering from leaking results.
            for match in date_matches:
                state.update(
                    match["home_team_norm"],
                    match["away_team_norm"],
                    int(match["home_goals"]),
                    int(match["away_goals"]),
                    home_shots=match.get("home_shots"),
                    away_shots=match.get("away_shots"),
                    home_shots_on_target=match.get("home_shots_on_target"),
                    away_shots_on_target=match.get("away_shots_on_target"),
                )
            state.source_max_match_date = match_date
    return sorted(predictions, key=lambda item: (item["match_date"], item["hist_match_id"]))


def fit_hyperparameters(
    matches: list[dict[str, Any]],
    *,
    validation_start_on: date,
    validation_end_on: date,
    alpha_grid: tuple[float, ...],
    rho_grid: tuple[float, ...],
    temperature_grid: tuple[float, ...],
    anchor_weight_grid: tuple[float, ...] = (0.0,),
    half_life_days: float = 365.0,
    prior_team_matches: float = 5.0,
    minimum_effective_matches: float = 80.0,
) -> dict[str, Any]:
    """Fit shot_alpha, rho, temperature and market anchor weight on a
    pre-test validation window.

    Every candidate is evaluated with the same leak-free walk-forward as the
    real test run, restricted to matches inside the validation window. Callers
    must pass only matches dated before the real test window; this function
    additionally refuses any match after ``validation_end_on``.
    """

    for match in matches:
        if match["match_date"] > validation_end_on:
            raise ValueError(
                "fit_hyperparameters received matches after the validation window; "
                "this would leak test data into hyperparameter selection"
            )
    if not alpha_grid or not rho_grid or not temperature_grid or not anchor_weight_grid:
        raise ValueError("hyperparameter grids must be non-empty")

    best: dict[str, Any] | None = None
    for alpha in alpha_grid:
        # rho and temperature do not influence state evolution, so one walk per
        # alpha is enough: expected goals are reused across the rho grid.
        base_predictions = run_walk_forward(
            matches,
            test_start_on=validation_start_on,
            test_end_on=validation_end_on,
            half_life_days=half_life_days,
            prior_team_matches=prior_team_matches,
            rho=rho_grid[0],
            shot_alpha=alpha,
            temperature=1.0,
            minimum_effective_matches=minimum_effective_matches,
        )
        if not base_predictions:
            continue
        for rho in rho_grid:
            probability_sets = [
                dixon_coles_probabilities(
                    prediction["expected_home_goals"],
                    prediction["expected_away_goals"],
                    rho,
                )
                for prediction in base_predictions
            ]
            for temperature in temperature_grid:
                tempered_sets = [
                    apply_temperature(probabilities, temperature)
                    for probabilities in probability_sets
                ]
                for anchor_weight in anchor_weight_grid:
                    log_loss_total = 0.0
                    for prediction, tempered in zip(base_predictions, tempered_sets):
                        blended = blend_with_market(
                            tempered,
                            prediction["market_anchor_probabilities"],
                            anchor_weight,
                        )
                        actual_index = RESULT_INDEX[prediction["actual_result"]]
                        log_loss_total += -log(max(1e-12, blended[actual_index]))
                    log_loss = log_loss_total / len(base_predictions)
                    if best is None or log_loss < best["_raw_log_loss"] - 1e-12:
                        best = {
                            "shot_alpha": alpha,
                            "rho": rho,
                            "temperature": temperature,
                            "market_anchor_weight": anchor_weight,
                            "_raw_log_loss": log_loss,
                            "validation_log_loss": round(log_loss, 6),
                            "validation_matches": len(base_predictions),
                            "validation_anchored_matches": sum(
                                1
                                for prediction in base_predictions
                                if prediction["market_anchor_probabilities"]
                            ),
                        }
    if best is None:
        raise ValueError("hyperparameter fitting produced no validation predictions")
    del best["_raw_log_loss"]
    best["validation_start_on"] = validation_start_on.isoformat()
    best["validation_end_on"] = validation_end_on.isoformat()
    return best


def summarize_predictions(
    predictions: list[dict[str, Any]],
    competition_names: dict[str, str],
) -> dict[str, Any]:
    if not predictions:
        raise ValueError("walk-forward run produced no predictions")

    headline = _metric_block(predictions)
    calibration, ece = _calibration(predictions)
    headline["ece"] = round(ece, 6)

    leagues = []
    for code in sorted({item["competition_code"] for item in predictions}):
        league_predictions = [item for item in predictions if item["competition_code"] == code]
        metrics = _metric_block(league_predictions)
        _, league_ece = _calibration(league_predictions)
        leagues.append(
            {
                "code": code,
                "name": competition_names.get(code, code),
                "matches": len(league_predictions),
                "brier": metrics["brier"],
                "log_loss": metrics["log_loss"],
                "ece": round(league_ece, 6),
                "market_brier": metrics["market_brier"],
                "market_log_loss": metrics["market_log_loss"],
            }
        )

    monthly: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for prediction in predictions:
        monthly[prediction["match_date"].strftime("%Y-%m")].append(prediction)
    evaluation_path = []
    for label in sorted(monthly):
        metrics = _metric_block(monthly[label])
        evaluation_path.append([label, metrics["brier"], metrics["market_brier"]])

    return {
        "headline": {
            **headline,
            "sample_matches": len(predictions),
        },
        "calibration": calibration,
        "evaluation_path": evaluation_path,
        "leagues": leagues,
    }


def _metric_block(predictions: Iterable[dict[str, Any]]) -> dict[str, float | None]:
    rows = list(predictions)
    brier_values: list[float] = []
    log_values: list[float] = []
    market_brier_values: list[float] = []
    market_log_values: list[float] = []
    for prediction in rows:
        actual_index = RESULT_INDEX[prediction["actual_result"]]
        probabilities = prediction["probabilities"]
        outcomes = tuple(1.0 if index == actual_index else 0.0 for index in range(3))
        brier_values.append(sum((probabilities[index] - outcomes[index]) ** 2 for index in range(3)) / 3)
        log_values.append(-log(max(1e-12, probabilities[actual_index])))
        market = prediction.get("market_probabilities")
        if market:
            market_brier_values.append(sum((market[index] - outcomes[index]) ** 2 for index in range(3)) / 3)
            market_log_values.append(-log(max(1e-12, market[actual_index])))

    return {
        "brier": round(sum(brier_values) / len(brier_values), 6),
        "log_loss": round(sum(log_values) / len(log_values), 6),
        "market_brier": (
            round(sum(market_brier_values) / len(market_brier_values), 6)
            if market_brier_values
            else None
        ),
        "market_log_loss": (
            round(sum(market_log_values) / len(market_log_values), 6)
            if market_log_values
            else None
        ),
    }


def _calibration(predictions: Iterable[dict[str, Any]]) -> tuple[list[list[float]], float]:
    bins: list[list[tuple[float, float]]] = [[] for _ in range(10)]
    for prediction in predictions:
        actual_index = RESULT_INDEX[prediction["actual_result"]]
        for index, probability in enumerate(prediction["probabilities"]):
            bucket = min(9, int(probability * 10))
            bins[bucket].append((probability, 1.0 if index == actual_index else 0.0))

    total = sum(len(bucket) for bucket in bins)
    calibration: list[list[float]] = []
    ece = 0.0
    for bucket in bins:
        if not bucket:
            continue
        predicted = sum(item[0] for item in bucket) / len(bucket)
        actual = sum(item[1] for item in bucket) / len(bucket)
        calibration.append([round(predicted, 6), round(actual, 6)])
        ece += len(bucket) / total * abs(predicted - actual)
    return calibration, ece


def current_team_form(
    matches: list[dict[str, Any]],
    current_teams: set[str],
    *,
    window: int,
) -> list[dict[str, Any]]:
    team_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for match in sorted(matches, key=lambda item: (item["match_date"], item["hist_match_id"]), reverse=True):
        if len(team_rows[match["home_team_norm"]]) < window:
            team_rows[match["home_team_norm"]].append(_team_match_view(match, home=True))
        if len(team_rows[match["away_team_norm"]]) < window:
            team_rows[match["away_team_norm"]].append(_team_match_view(match, home=False))

    selected_rows = [row for team in current_teams for row in team_rows.get(team, [])]
    league_goals_per_team_match = (
        sum(row["goals_for"] for row in selected_rows) / len(selected_rows)
        if selected_rows
        else 1.25
    )
    output: list[dict[str, Any]] = []
    for team_key in sorted(current_teams):
        rows = team_rows.get(team_key, [])
        if not rows:
            continue
        matches_played = len(rows)
        goals_for = sum(row["goals_for"] for row in rows)
        goals_against = sum(row["goals_against"] for row in rows)
        wins = sum(row["points"] == 3 for row in rows)
        draws = sum(row["points"] == 1 for row in rows)
        losses = matches_played - wins - draws
        goals_for_rate = goals_for / matches_played
        goals_against_rate = goals_against / matches_played
        attack_index = _clamp(100 * goals_for_rate / max(0.25, league_goals_per_team_match), 50, 150)
        defense_index = _clamp(
            100 * league_goals_per_team_match / max(0.25, goals_against_rate),
            50,
            150,
        )
        recent = rows[: min(5, matches_played)]
        previous = rows[min(5, matches_played) : min(10, matches_played)]
        recent_ppg = sum(row["points"] for row in recent) / len(recent)
        previous_ppg = (
            sum(row["points"] for row in previous) / len(previous)
            if previous
            else recent_ppg
        )
        first = rows[0]
        output.append(
            {
                "team_key": team_key,
                "name_original": first["team_name"],
                "matches": matches_played,
                "wins": wins,
                "draws": draws,
                "losses": losses,
                "goals_for_per_match": round(goals_for_rate, 2),
                "goals_against_per_match": round(goals_against_rate, 2),
                "points_per_match": round(sum(row["points"] for row in rows) / matches_played, 2),
                "recent_points_per_match": round(recent_ppg, 2),
                "attack_index": round(attack_index, 1),
                "defense_index": round(defense_index, 1),
                "overall_index": round(sqrt(attack_index * defense_index), 1),
                "momentum": round(recent_ppg - previous_ppg, 2),
                "confidence": round(min(1.0, matches_played / window), 3),
            }
        )
    return sorted(output, key=lambda item: (item["overall_index"], item["points_per_match"]), reverse=True)


def _team_match_view(match: dict[str, Any], *, home: bool) -> dict[str, Any]:
    goals_for = int(match["home_goals"] if home else match["away_goals"])
    goals_against = int(match["away_goals"] if home else match["home_goals"])
    return {
        "team_name": match["home_team"] if home else match["away_team"],
        "goals_for": goals_for,
        "goals_against": goals_against,
        "points": 3 if goals_for > goals_against else 1 if goals_for == goals_against else 0,
    }
