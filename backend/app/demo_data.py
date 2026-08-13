from __future__ import annotations

MATCHES = [
    {
        "match_id": "m-1042",
        "competition": "英超",
        "kickoff_utc": "2026-07-20T12:30:00Z",
        "home_team": "阿森纳",
        "away_team": "罗马",
        "status": "scheduled",
        "model_probability": {"home": 0.59, "draw": 0.23, "away": 0.18},
        "market_probability": {"home": 0.53, "draw": 0.27, "away": 0.20},
        "edge": 0.06,
        "data_completeness": 0.994,
        "lineup_confidence": 0.82,
        "calibration_status": "stable",
    },
    {
        "match_id": "m-1043",
        "competition": "西甲",
        "kickoff_utc": "2026-07-20T14:15:00Z",
        "home_team": "皇家社会",
        "away_team": "比利亚雷亚尔",
        "status": "scheduled",
        "model_probability": {"home": 0.44, "draw": 0.30, "away": 0.26},
        "market_probability": {"home": 0.42, "draw": 0.31, "away": 0.27},
        "edge": 0.02,
        "data_completeness": 0.988,
        "lineup_confidence": 0.74,
        "calibration_status": "stable",
    },
]

BACKTEST = {
    "model_version": "dc-gbdt-2.3.1",
    "validation": "rolling_time_series",
    "roi": 0.078,
    "mean_clv": 0.024,
    "max_drawdown": -0.086,
    "brier_score": 0.184,
    "bet_count": 386,
}

OPS = {
    "status": "healthy_with_warnings",
    "core_completeness": 0.994,
    "job_success_rate_24h": 0.997,
    "mapping_conflicts": 12,
    "last_audit_utc": "2026-07-20T08:22:18Z",
}

