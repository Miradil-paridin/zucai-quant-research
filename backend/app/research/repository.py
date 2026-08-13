from __future__ import annotations

from datetime import date
from typing import Any

from psycopg.types.json import Jsonb

from ..database import connect


SUPPORTED_COMPETITION_CODES = ("PL", "PD", "BL1", "SA", "FL1", "SE1", "NO1", "FI1")
BIG_FIVE_CODES = ("PL", "PD", "BL1", "SA", "FL1")


class ResearchRepository:
    def competition_catalog(self) -> list[dict[str, Any]]:
        with connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT competition_code,
                           max(match_date) AS latest_match_date,
                           min(match_date) AS first_match_date,
                           count(*) AS matches
                    FROM core.historical_matches
                    WHERE competition_code = ANY(%s)
                    GROUP BY competition_code
                    ORDER BY competition_code
                    """,
                    (list(SUPPORTED_COMPETITION_CODES),),
                ).fetchall()
            )

    def load_model_matches(
        self,
        *,
        train_start_on: date,
        test_end_on: date,
        competition_codes: tuple[str, ...] = BIG_FIVE_CODES,
    ) -> list[dict[str, Any]]:
        with connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT hm.hist_match_id, hm.competition_code, hm.match_date,
                           hm.home_team_norm, hm.away_team_norm,
                           hm.home_goals, hm.away_goals, hm.full_time_result,
                           hm.source_row_hash,
                           CASE WHEN hm.match_stats->>'HS' ~ '^[0-9]+(\\.[0-9]+)?$'
                                THEN (hm.match_stats->>'HS')::float8 END AS home_shots,
                           CASE WHEN hm.match_stats->>'AS' ~ '^[0-9]+(\\.[0-9]+)?$'
                                THEN (hm.match_stats->>'AS')::float8 END AS away_shots,
                           CASE WHEN hm.match_stats->>'HST' ~ '^[0-9]+(\\.[0-9]+)?$'
                                THEN (hm.match_stats->>'HST')::float8 END AS home_shots_on_target,
                           CASE WHEN hm.match_stats->>'AST' ~ '^[0-9]+(\\.[0-9]+)?$'
                                THEN (hm.match_stats->>'AST')::float8 END AS away_shots_on_target,
                           ho.odd_home::float8 AS closing_odd_home,
                           ho.odd_draw::float8 AS closing_odd_draw,
                           ho.odd_away::float8 AS closing_odd_away,
                           op.odd_home::float8 AS opening_odd_home,
                           op.odd_draw::float8 AS opening_odd_draw,
                           op.odd_away::float8 AS opening_odd_away
                    FROM core.historical_matches hm
                    LEFT JOIN core.historical_odds ho
                      ON ho.hist_match_id = hm.hist_match_id
                     AND ho.bookmaker = 'MarketAverage'
                     AND ho.market_type = '1X2'
                     AND ho.price_timing = 'closing'
                    LEFT JOIN core.historical_odds op
                      ON op.hist_match_id = hm.hist_match_id
                     AND op.bookmaker = 'MarketAverage'
                     AND op.market_type = '1X2'
                     AND op.price_timing = 'opening'
                    WHERE hm.competition_code = ANY(%s)
                      AND hm.match_date BETWEEN %s AND %s
                      AND hm.result_status NOT IN ('awarded', 'abandoned')
                    ORDER BY hm.competition_code, hm.match_date, hm.hist_match_id
                    """,
                    (list(competition_codes), train_start_on, test_end_on),
                ).fetchall()
            )

    def save_backtest(
        self,
        *,
        run_key: str,
        model_name: str,
        model_version: str,
        train_start_on: date,
        train_end_on: date,
        test_start_on: date,
        test_end_on: date,
        source_fingerprint: str,
        odds_fingerprint: str,
        implementation_fingerprint: str,
        prediction_content_sha256: str,
        configuration: dict[str, Any],
        metrics: dict[str, Any],
        predictions: list[dict[str, Any]],
    ) -> int:
        with connect() as connection:
            existing = connection.execute(
                """
                SELECT historical_backtest_run_id, prediction_content_sha256
                FROM analytics.historical_backtest_runs
                WHERE run_key = %s
                FOR UPDATE
                """,
                (run_key,),
            ).fetchone()
            if existing:
                existing_content = existing["prediction_content_sha256"]
                if existing_content and existing_content != prediction_content_sha256:
                    raise RuntimeError(
                        "determinism violation: identical run inputs produced different predictions"
                    )
                run_id = int(existing["historical_backtest_run_id"])
                connection.execute(
                    """
                    UPDATE analytics.historical_backtest_runs
                    SET model_name = %s,
                        model_version = %s,
                        source_fingerprint = %s,
                        odds_fingerprint = %s,
                        implementation_fingerprint = %s,
                        prediction_content_sha256 = %s,
                        configuration = %s,
                        metrics = %s,
                        status = 'completed',
                        completed_at_utc = now()
                    WHERE historical_backtest_run_id = %s
                    """,
                    (
                        model_name,
                        model_version,
                        source_fingerprint,
                        odds_fingerprint,
                        implementation_fingerprint,
                        prediction_content_sha256,
                        Jsonb(configuration),
                        Jsonb(metrics),
                        run_id,
                    ),
                )
            else:
                row = connection.execute(
                    """
                    INSERT INTO analytics.historical_backtest_runs (
                        run_key, model_name, model_version,
                        train_start_on, train_end_on, test_start_on, test_end_on,
                        source_fingerprint, odds_fingerprint,
                        implementation_fingerprint, prediction_content_sha256,
                        configuration, metrics, status, completed_at_utc
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, 'completed', now()
                    )
                    RETURNING historical_backtest_run_id
                    """,
                    (
                        run_key,
                        model_name,
                        model_version,
                        train_start_on,
                        train_end_on,
                        test_start_on,
                        test_end_on,
                        source_fingerprint,
                        odds_fingerprint,
                        implementation_fingerprint,
                        prediction_content_sha256,
                        Jsonb(configuration),
                        Jsonb(metrics),
                    ),
                ).fetchone()
                run_id = int(row["historical_backtest_run_id"])
            connection.execute(
                """
                DELETE FROM analytics.historical_model_predictions
                WHERE historical_backtest_run_id = %s
                """,
                (run_id,),
            )
            prediction_rows = []
            for prediction in predictions:
                model = prediction["probabilities"]
                market = prediction.get("market_probabilities")
                prediction_rows.append(
                    (
                        run_id,
                        prediction["hist_match_id"],
                        prediction["match_date"],
                        prediction["training_cutoff_on"],
                        prediction["source_max_match_date"],
                        model[0],
                        model[1],
                        model[2],
                        prediction["expected_home_goals"],
                        prediction["expected_away_goals"],
                        market[0] if market else None,
                        market[1] if market else None,
                        market[2] if market else None,
                        prediction["actual_result"],
                    )
                )
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO analytics.historical_model_predictions (
                        historical_backtest_run_id, hist_match_id, prediction_for_on,
                        training_cutoff_on, source_max_match_date,
                        probability_home, probability_draw, probability_away,
                        expected_home_goals, expected_away_goals,
                        market_probability_home, market_probability_draw, market_probability_away,
                        actual_result
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    )
                    """,
                    prediction_rows,
                )
            return run_id

    def latest_backtest(self) -> dict[str, Any] | None:
        with connect() as connection:
            return connection.execute(
                """
                SELECT historical_backtest_run_id, run_key, model_name, model_version,
                       train_start_on, train_end_on, test_start_on, test_end_on,
                       source_fingerprint, odds_fingerprint,
                       implementation_fingerprint, prediction_content_sha256,
                       configuration, metrics, status,
                       created_at_utc, completed_at_utc
                FROM analytics.historical_backtest_runs
                WHERE status = 'completed'
                ORDER BY completed_at_utc DESC, historical_backtest_run_id DESC
                LIMIT 1
                """
            ).fetchone()

    def backtest_integrity(self, run_id: int) -> dict[str, Any]:
        with connect() as connection:
            return connection.execute(
                """
                SELECT count(*) AS predictions,
                       count(*) FILTER (
                           WHERE p.training_cutoff_on >= hm.match_date
                              OR p.source_max_match_date >= hm.match_date
                              OR p.prediction_for_on <> hm.match_date
                       ) AS cutoff_violations,
                       count(*) FILTER (
                           WHERE abs(
                               p.probability_home
                               + p.probability_draw
                               + p.probability_away
                               - 1
                           ) >= 0.000001
                       ) AS probability_violations,
                       count(*) FILTER (
                           WHERE p.market_probability_home IS NOT NULL
                       ) AS market_benchmarks,
                       count(*) FILTER (
                           WHERE hm.result_status <> 'unverified'
                       ) AS cross_source_verified_results,
                       count(*) FILTER (
                           WHERE hm.result_status = 'unverified'
                       ) AS single_source_results
                FROM analytics.historical_model_predictions p
                JOIN core.historical_matches hm USING (hist_match_id)
                WHERE p.historical_backtest_run_id = %s
                """,
                (run_id,),
            ).fetchone()

    def team_form_source(
        self,
        *,
        competition_code: str,
        as_of: date | None,
    ) -> tuple[date, str, set[str], list[dict[str, Any]]]:
        with connect() as connection:
            latest = connection.execute(
                """
                SELECT match_date, season_label
                FROM core.historical_matches
                WHERE competition_code = %s
                  AND (%s::date IS NULL OR match_date <= %s)
                ORDER BY match_date DESC, hist_match_id DESC
                LIMIT 1
                """,
                (competition_code, as_of, as_of),
            ).fetchone()
            if not latest:
                raise LookupError(f"no historical matches for {competition_code}")
            effective_as_of = latest["match_date"]
            latest_season = latest["season_label"]
            current_rows = connection.execute(
                """
                SELECT home_team_norm, away_team_norm
                FROM core.historical_matches
                WHERE competition_code = %s
                  AND season_label = %s
                  AND match_date <= %s
                """,
                (competition_code, latest_season, effective_as_of),
            ).fetchall()
            current_teams = {
                team
                for row in current_rows
                for team in (row["home_team_norm"], row["away_team_norm"])
            }
            matches = list(
                connection.execute(
                    """
                    SELECT hist_match_id, match_date, home_team, away_team,
                           home_team_norm, away_team_norm, home_goals, away_goals
                    FROM core.historical_matches
                    WHERE competition_code = %s
                      AND match_date <= %s
                    ORDER BY match_date DESC, hist_match_id DESC
                    """,
                    (competition_code, effective_as_of),
                ).fetchall()
            )
        return effective_as_of, latest_season, current_teams, matches

    def list_historical_matches(
        self,
        *,
        competition_code: str | None,
        season_label: str | None,
        date_to: date | None,
        limit: int,
        offset: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        clauses = ["1 = 1"]
        parameters: list[Any] = []
        if competition_code:
            clauses.append("hm.competition_code = %s")
            parameters.append(competition_code)
        if season_label:
            clauses.append("hm.season_label = %s")
            parameters.append(season_label)
        if date_to:
            clauses.append("hm.match_date <= %s")
            parameters.append(date_to)
        where = " AND ".join(clauses)
        with connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT count(*) AS total FROM core.historical_matches hm WHERE {where}",
                    parameters,
                ).fetchone()["total"]
            )
            rows = list(
                connection.execute(
                    f"""
                    SELECT hm.hist_match_id, hm.competition_code, hm.season_label,
                           hm.match_date, hm.match_time_local, hm.source_timezone,
                           hm.home_team, hm.away_team, hm.home_goals, hm.away_goals,
                           hm.ht_home_goals, hm.ht_away_goals, hm.full_time_result,
                           hm.result_status, hm.match_stats, hm.quality_flags,
                           opening.odd_home::float8 AS opening_odd_home,
                           opening.odd_draw::float8 AS opening_odd_draw,
                           opening.odd_away::float8 AS opening_odd_away,
                           closing.odd_home::float8 AS closing_odd_home,
                           closing.odd_draw::float8 AS closing_odd_draw,
                           closing.odd_away::float8 AS closing_odd_away
                    FROM core.historical_matches hm
                    LEFT JOIN core.historical_odds opening
                      ON opening.hist_match_id = hm.hist_match_id
                     AND opening.bookmaker = 'MarketAverage'
                     AND opening.market_type = '1X2'
                     AND opening.price_timing = 'opening'
                    LEFT JOIN core.historical_odds closing
                      ON closing.hist_match_id = hm.hist_match_id
                     AND closing.bookmaker = 'MarketAverage'
                     AND closing.market_type = '1X2'
                     AND closing.price_timing = 'closing'
                    WHERE {where}
                    ORDER BY hm.match_date DESC, hm.match_time_local DESC NULLS LAST,
                             hm.hist_match_id DESC
                    LIMIT %s OFFSET %s
                    """,
                    [*parameters, limit, offset],
                ).fetchall()
            )
        return total, rows


research_repository = ResearchRepository()
