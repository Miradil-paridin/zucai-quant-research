from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from ..localization import localize_team
from .model import (
    RESULT_INDEX,
    current_team_form,
    fit_hyperparameters,
    run_walk_forward,
    summarize_predictions,
)
from .repository import BIG_FIVE_CODES, SUPPORTED_COMPETITION_CODES, research_repository


COMPETITIONS = {
    "PL": {"name": "英格兰超级联赛", "country": "英格兰"},
    "PD": {"name": "西班牙甲级联赛", "country": "西班牙"},
    "BL1": {"name": "德国甲级联赛", "country": "德国"},
    "SA": {"name": "意大利甲级联赛", "country": "意大利"},
    "FL1": {"name": "法国甲级联赛", "country": "法国"},
    "SE1": {"name": "瑞典超级联赛", "country": "瑞典"},
    "NO1": {"name": "挪威超级联赛", "country": "挪威"},
    "FI1": {"name": "芬兰超级联赛", "country": "芬兰"},
}

DEFAULT_BACKTEST = {
    "model_name": (
        "Rolling Poisson/DC with shot-blended strengths, "
        "market-anchored on de-vigged opening odds, "
        "validation-fitted rho/temperature/anchor weight"
    ),
    "model_version": "rolling-poisson-dc-v3-market-anchor",
    "train_start_on": date(2019, 7, 1),
    "train_end_on": date(2025, 7, 31),
    "test_start_on": date(2025, 8, 1),
    "test_end_on": date(2026, 6, 30),
    "validation_start_on": date(2024, 8, 1),
    "validation_end_on": date(2025, 6, 30),
    "competition_codes": list(BIG_FIVE_CODES),
    "half_life_days": 365.0,
    "prior_team_matches": 5.0,
    "shot_alpha_grid": [1.0, 0.8, 0.65, 0.5, 0.35],
    "rho_grid": [-0.16, -0.12, -0.08, -0.05, -0.02, 0.0],
    "temperature_grid": [0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2],
    "anchor_weight_grid": [0.0, 0.3, 0.5, 0.7, 0.85, 0.95, 1.0],
    "minimum_effective_matches": 80.0,
    "same_date_results_excluded": True,
    "historical_odds_used_as_features": "opening_only_as_blend_prior",
    "odds_feature_policy": (
        "De-vigged MarketAverage OPENING 1X2 odds are used as a geometric "
        "blend prior (they exist before match day); CLOSING odds are never "
        "a model input and remain an ex-post benchmark only."
    ),
    "hyperparameters_fitted_on": "pre-test validation window only",
    "market_benchmark": "MarketAverage closing 1X2, de-vigged, ex-post diagnostic only",
    "brier_definition": "mean_per_class_multiclass_brier",
    "ece_definition": "pooled_one_vs_rest_fixed_width_10_bins",
}


def run_default_backtest() -> dict[str, Any]:
    config = DEFAULT_BACKTEST
    matches = research_repository.load_model_matches(
        train_start_on=config["train_start_on"],
        test_end_on=config["test_end_on"],
        competition_codes=tuple(config["competition_codes"]),
    )
    source_fingerprint = _source_fingerprint(matches)
    odds_fingerprint = _odds_fingerprint(matches)
    implementation_fingerprint = _implementation_fingerprint()
    pre_test_matches = [
        match for match in matches if match["match_date"] < config["test_start_on"]
    ]
    fitted = fit_hyperparameters(
        pre_test_matches,
        validation_start_on=config["validation_start_on"],
        validation_end_on=config["validation_end_on"],
        alpha_grid=tuple(config["shot_alpha_grid"]),
        rho_grid=tuple(config["rho_grid"]),
        temperature_grid=tuple(config["temperature_grid"]),
        anchor_weight_grid=tuple(config["anchor_weight_grid"]),
        half_life_days=config["half_life_days"],
        prior_team_matches=config["prior_team_matches"],
        minimum_effective_matches=config["minimum_effective_matches"],
    )
    predictions = run_walk_forward(
        matches,
        test_start_on=config["test_start_on"],
        test_end_on=config["test_end_on"],
        half_life_days=config["half_life_days"],
        prior_team_matches=config["prior_team_matches"],
        rho=fitted["rho"],
        shot_alpha=fitted["shot_alpha"],
        temperature=fitted["temperature"],
        market_anchor_weight=fitted["market_anchor_weight"],
        minimum_effective_matches=config["minimum_effective_matches"],
    )
    metrics = summarize_predictions(
        predictions,
        {code: value["name"] for code, value in COMPETITIONS.items()},
    )
    metrics["hyperparameters"] = fitted
    metrics["anchor_diagnostics"] = _anchor_diagnostics(
        predictions, fitted["market_anchor_weight"]
    )
    prediction_content_sha256 = _prediction_content_fingerprint(predictions)
    configuration = {
        key: value.isoformat() if isinstance(value, date) else value
        for key, value in config.items()
    }
    run_key = hashlib.sha256(
        json.dumps(
            {
                "configuration": configuration,
                "source_fingerprint": source_fingerprint,
                "odds_fingerprint": odds_fingerprint,
                "implementation_fingerprint": implementation_fingerprint,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    run_id = research_repository.save_backtest(
        run_key=run_key,
        model_name=config["model_name"],
        model_version=config["model_version"],
        train_start_on=config["train_start_on"],
        train_end_on=config["train_end_on"],
        test_start_on=config["test_start_on"],
        test_end_on=config["test_end_on"],
        source_fingerprint=source_fingerprint,
        odds_fingerprint=odds_fingerprint,
        implementation_fingerprint=implementation_fingerprint,
        prediction_content_sha256=prediction_content_sha256,
        configuration=configuration,
        metrics=metrics,
        predictions=predictions,
    )
    integrity = research_repository.backtest_integrity(run_id)
    if (
        int(integrity["predictions"]) != len(predictions)
        or int(integrity["cutoff_violations"]) != 0
        or int(integrity["probability_violations"]) != 0
    ):
        raise RuntimeError(f"persisted walk-forward integrity check failed: {integrity}")
    return backtest_response(
        {
            **research_repository.latest_backtest(),
            "integrity": integrity,
            "current_fingerprints": {
                "source_fingerprint": source_fingerprint,
                "odds_fingerprint": odds_fingerprint,
                "implementation_fingerprint": implementation_fingerprint,
            },
        }
    )


def latest_backtest_response() -> dict[str, Any]:
    run = research_repository.latest_backtest()
    if not run:
        return {
            "data_status": "not_computed",
            "status": "missing",
            "message": "真实历史回测尚未执行。",
        }
    integrity = research_repository.backtest_integrity(
        int(run["historical_backtest_run_id"])
    )
    configuration = run["configuration"]
    current_matches = research_repository.load_model_matches(
        train_start_on=date.fromisoformat(configuration["train_start_on"]),
        test_end_on=date.fromisoformat(configuration["test_end_on"]),
        competition_codes=tuple(configuration["competition_codes"]),
    )
    current_fingerprints = {
        "source_fingerprint": _source_fingerprint(current_matches),
        "odds_fingerprint": _odds_fingerprint(current_matches),
        "implementation_fingerprint": _implementation_fingerprint(),
    }
    return backtest_response(
        {
            **run,
            "integrity": integrity,
            "current_fingerprints": current_fingerprints,
        }
    )


def backtest_response(run: dict[str, Any]) -> dict[str, Any]:
    metrics = run["metrics"]
    integrity = run["integrity"]
    current = run["current_fingerprints"]
    source_current = run["source_fingerprint"] == current["source_fingerprint"]
    odds_current = run["odds_fingerprint"] == current["odds_fingerprint"]
    implementation_current = (
        run["implementation_fingerprint"] == current["implementation_fingerprint"]
    )
    probability_metrics_valid = (
        int(integrity["cutoff_violations"]) == 0
        and int(integrity["probability_violations"]) == 0
        and source_current
        and implementation_current
    )
    verified_results = int(integrity["cross_source_verified_results"])
    single_source_results = int(integrity["single_source_results"])
    if single_source_results == 0 and verified_results == int(integrity["predictions"]):
        verification_status = "cross_source_verified"
    elif verified_results:
        verification_status = "mixed_verification"
    else:
        verification_status = "single_source_hash_pinned"
    return {
        "data_status": "real_historical_backtest",
        "status": run["status"],
        "run_id": int(run["historical_backtest_run_id"]),
        "run_key": run["run_key"],
        "model": {
            "name": run["model_name"],
            "version": run["model_version"],
        },
        "scope": {
            "train_start_on": run["train_start_on"],
            "train_end_on": run["train_end_on"],
            "test_start_on": run["test_start_on"],
            "test_end_on": run["test_end_on"],
            "matches": metrics["headline"]["sample_matches"],
            "competitions": run["configuration"]["competition_codes"],
        },
        "leakage_policy": {
            "same_date_results_excluded": True,
            "historical_odds_used_as_features": run["configuration"].get(
                "historical_odds_used_as_features", False
            ),
            "odds_feature_policy": run["configuration"].get("odds_feature_policy"),
            "training_cutoff_strictly_before_match": int(integrity["cutoff_violations"]) == 0,
        },
        "safety": {
            "probability_metrics_valid": probability_metrics_valid,
            "market_metrics_valid": probability_metrics_valid and odds_current,
            "roi_available": False,
            "clv_available": False,
            "max_drawdown_available": False,
            "betting_status": "blocked_no_point_in_time_safe_odds",
            "reason": (
                "历史赔率缺少逐条采集时间证明，仅作为赛后市场概率基准；"
                "不能用于模拟成交、ROI、CLV 或最大回撤。"
            ),
        },
        "betting": {
            "status": "blocked_no_point_in_time_safe_odds",
            "bet_count": 0,
            "roi": None,
            "clv": None,
            "max_drawdown": None,
        },
        "provenance": {
            "source": "core.historical_matches + core.historical_odds",
            "source_fingerprint": run["source_fingerprint"],
            "odds_fingerprint": run["odds_fingerprint"],
            "implementation_fingerprint": run["implementation_fingerprint"],
            "prediction_content_sha256": run["prediction_content_sha256"],
            "source_fingerprint_current": source_current,
            "odds_fingerprint_current": odds_current,
            "implementation_fingerprint_current": implementation_current,
            "market_benchmark_status": "ex_post_market_diagnostic",
            "market_benchmarks": int(integrity["market_benchmarks"]),
            "persisted_predictions": int(integrity["predictions"]),
            "cross_source_verified_results": verified_results,
            "single_source_results": single_source_results,
            "result_verification_status": verification_status,
        },
        "result_verification": {
            "verified_normal": verified_results,
            "single_source_unverified": single_source_results,
            "status": verification_status,
        },
        "metric_definitions": {
            "brier": run["configuration"].get(
                "brier_definition", "mean_per_class_multiclass_brier"
            ),
            "ece": run["configuration"].get(
                "ece_definition", "pooled_one_vs_rest_fixed_width_10_bins"
            ),
        },
        "hyperparameters": metrics.get("hyperparameters"),
        "anchor_diagnostics": metrics.get("anchor_diagnostics"),
        "headline": metrics["headline"],
        "calibration": metrics["calibration"],
        "evaluation_path": metrics["evaluation_path"],
        "leagues": metrics["leagues"],
        "completed_at_utc": run["completed_at_utc"],
    }


def teams_response(
    *,
    competition_code: str,
    as_of: date | None,
    window: int,
) -> dict[str, Any]:
    if competition_code not in SUPPORTED_COMPETITION_CODES:
        raise ValueError(f"unsupported competition code: {competition_code}")
    effective_as_of, season_label, current_teams, matches = research_repository.team_form_source(
        competition_code=competition_code,
        as_of=as_of,
    )
    items = current_team_form(matches, current_teams, window=window)
    for item in items:
        item["name"] = localize_team(item["name_original"])
    catalog = research_repository.competition_catalog()
    available = [
        {
            "code": item["competition_code"],
            "name": COMPETITIONS[item["competition_code"]]["name"],
            "latest_match_date": item["latest_match_date"],
        }
        for item in sorted(
            catalog,
            key=lambda row: SUPPORTED_COMPETITION_CODES.index(row["competition_code"]),
        )
    ]
    return {
        "data_status": "real_historical_computed",
        "source": "core.historical_matches",
        "method": "rolling-results-baseline-v1",
        "competition": competition_code,
        "competition_name": COMPETITIONS[competition_code]["name"],
        "season_label": season_label,
        "as_of_date": effective_as_of,
        "window_matches": window,
        "available_competitions": available,
        "limitations": [
            "仅使用目标日期之前的真实赛果计算。",
            "攻击、防守和整体指数以联赛均值 100 为基准。",
            "尚未包含 xG、历史阵容、球员评分、伤停和赛程强度修正。",
            "该页面是球队历史表现基线，不是投注建议。",
        ],
        "items": items,
    }


def historical_matches_response(
    *,
    competition_code: str | None,
    season_label: str | None,
    date_to: date | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    if competition_code and competition_code not in SUPPORTED_COMPETITION_CODES:
        raise ValueError(f"unsupported competition code: {competition_code}")
    total, rows = research_repository.list_historical_matches(
        competition_code=competition_code,
        season_label=season_label,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    items = []
    for row in rows:
        opening = _odds_block(row, "opening")
        closing = _odds_block(row, "closing")
        items.append(
            {
                "historical_match_id": int(row["hist_match_id"]),
                "competition_code": row["competition_code"],
                "competition": COMPETITIONS[row["competition_code"]]["name"],
                "country": COMPETITIONS[row["competition_code"]]["country"],
                "season_label": row["season_label"],
                "match_date": row["match_date"],
                "match_time_local": row["match_time_local"],
                "source_timezone": row["source_timezone"],
                "home_team": {
                    "name": localize_team(row["home_team"]),
                    "name_original": row["home_team"],
                },
                "away_team": {
                    "name": localize_team(row["away_team"]),
                    "name_original": row["away_team"],
                },
                "score": {
                    "home": int(row["home_goals"]),
                    "away": int(row["away_goals"]),
                    "half_time_home": row["ht_home_goals"],
                    "half_time_away": row["ht_away_goals"],
                },
                "result": row["full_time_result"],
                "result_status": row["result_status"],
                "statistics": row["match_stats"],
                "quality_flags": row["quality_flags"],
                "odds": {
                    "bookmaker": "MarketAverage",
                    "opening": opening,
                    "closing": closing,
                    "point_in_time_safe": False,
                },
            }
        )
    return {
        "data_status": "real_historical_rows",
        "source": "football-data.co.uk",
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


def _odds_block(row: dict[str, Any], prefix: str) -> dict[str, float] | None:
    values = (
        row.get(f"{prefix}_odd_home"),
        row.get(f"{prefix}_odd_draw"),
        row.get(f"{prefix}_odd_away"),
    )
    if any(value is None for value in values):
        return None
    return {"home": values[0], "draw": values[1], "away": values[2]}


def _mean_brier(rows: list[tuple[tuple[float, float, float], int]]) -> float | None:
    if not rows:
        return None
    total = 0.0
    for probabilities, actual_index in rows:
        outcomes = tuple(1.0 if index == actual_index else 0.0 for index in range(3))
        total += sum(
            (probabilities[index] - outcomes[index]) ** 2 for index in range(3)
        ) / 3
    return round(total / len(rows), 6)


def _anchor_diagnostics(
    predictions: list[dict[str, Any]], anchor_weight: float
) -> dict[str, Any]:
    """Compare blended, model-only, opening-market and closing-market Brier.

    The decisive comparison for the CLV route is blended vs opening market on
    the anchored subset: if the blend beats the opening prior it is built on,
    the model adds information beyond the opening price.
    """

    anchored = [p for p in predictions if p.get("market_anchor_probabilities")]
    anchored_closing = [p for p in anchored if p.get("market_probabilities")]

    def rows(source: list[dict[str, Any]], key: str) -> list[tuple[Any, int]]:
        return [
            (p[key], RESULT_INDEX[p["actual_result"]])
            for p in source
            if p.get(key)
        ]

    return {
        "market_anchor_weight": anchor_weight,
        "matches_total": len(predictions),
        "matches_anchored": len(anchored),
        "matches_unanchored": len(predictions) - len(anchored),
        "model_only_brier_all": _mean_brier(rows(predictions, "model_probabilities")),
        "anchored_subset": {
            "blended_brier": _mean_brier(rows(anchored, "probabilities")),
            "model_only_brier": _mean_brier(rows(anchored, "model_probabilities")),
            "opening_market_brier": _mean_brier(
                rows(anchored, "market_anchor_probabilities")
            ),
            "closing_market_brier": _mean_brier(
                rows(anchored_closing, "market_probabilities")
            ),
        },
    }


def _source_fingerprint(matches: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for match in matches:
        digest.update(
            (
                f"{match['hist_match_id']}|{match['competition_code']}|"
                f"{match['match_date'].isoformat()}|{match['source_row_hash']}\n"
            ).encode("utf-8")
        )
    return digest.hexdigest()


def _odds_fingerprint(matches: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for match in matches:
        odds = (
            _fixed_fingerprint_number(match.get("closing_odd_home"), 8),
            _fixed_fingerprint_number(match.get("closing_odd_draw"), 8),
            _fixed_fingerprint_number(match.get("closing_odd_away"), 8),
        )
        digest.update(
            (
                f"{match['hist_match_id']}|{odds[0]}|{odds[1]}|{odds[2]}\n"
            ).encode("utf-8")
        )
    return digest.hexdigest()


def _implementation_fingerprint() -> str:
    digest = hashlib.sha256()
    research_root = Path(__file__).resolve().parent
    for filename in ("model.py", "repository.py", "service.py"):
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update((research_root / filename).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _prediction_content_fingerprint(predictions: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for prediction in sorted(predictions, key=lambda item: item["hist_match_id"]):
        model = prediction["probabilities"]
        market = prediction.get("market_probabilities")
        values = [
            str(prediction["hist_match_id"]),
            prediction["match_date"].isoformat(),
            prediction["training_cutoff_on"].isoformat(),
            prediction["source_max_match_date"].isoformat(),
            *(_fixed_fingerprint_number(value, 8) for value in model),
            _fixed_fingerprint_number(prediction["expected_home_goals"], 6),
            _fixed_fingerprint_number(prediction["expected_away_goals"], 6),
            *(
                _fixed_fingerprint_number(value, 8)
                for value in (market if market else (None, None, None))
            ),
            prediction["actual_result"],
        ]
        digest.update(("|".join(values) + "\n").encode("utf-8"))
    return digest.hexdigest()


def _fixed_fingerprint_number(value: float | None, digits: int) -> str:
    if value is None:
        return "NULL"
    return f"{float(value):.{digits}f}"
