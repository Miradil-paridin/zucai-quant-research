from __future__ import annotations

from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from ..database import connect
from .football_data_couk import DownloadedSourceFile, ParsedHistoricalFile, SOURCE_NAME


class HistoricalRepository:
    def start_import_run(self, requested_files: int) -> int:
        with connect() as connection:
            row = connection.execute(
                """
                INSERT INTO raw.historical_import_runs (source_name, requested_files, status)
                VALUES (%s, %s, 'running')
                RETURNING import_run_id
                """,
                (SOURCE_NAME, requested_files),
            ).fetchone()
            connection.commit()
        return int(row["import_run_id"])

    def finish_import_run(
        self,
        import_run_id: int,
        *,
        imported_files: int,
        matches_upserted: int,
        odds_upserted: int,
        report: dict[str, Any],
        status: str = "succeeded",
        error_summary: str | None = None,
    ) -> None:
        with connect() as connection:
            connection.execute(
                """
                UPDATE raw.historical_import_runs
                SET finished_at_utc = now(),
                    imported_files = %s,
                    matches_upserted = %s,
                    odds_upserted = %s,
                    status = %s,
                    error_summary = %s,
                    report = %s
                WHERE import_run_id = %s
                """,
                (
                    imported_files,
                    matches_upserted,
                    odds_upserted,
                    status,
                    error_summary,
                    Jsonb(report),
                    import_run_id,
                ),
            )
            connection.commit()

    def persist_file(self, parsed: ParsedHistoricalFile) -> dict[str, Any]:
        rejected_count = int(parsed.validation_report.get("rejected_count") or 0)
        if rejected_count:
            raise ValueError(
                f"refusing partial persistence: {rejected_count} non-empty source rows were rejected"
            )

        with connect() as connection:
            source_file_id = self._upsert_source_file(connection, parsed)
            team_map = self._ensure_team_map(connection, parsed, source_file_id)
            odds_rows: list[tuple[Any, ...]] = []
            imported_match_ids: list[int] = []
            imported_partition_ids: dict[tuple[str, str], set[int]] = {}
            unresolved: set[str] = set()

            for record in parsed.records:
                home_team_id = team_map.get(record.home_team_norm)
                away_team_id = team_map.get(record.away_team_norm)
                if home_team_id is None:
                    unresolved.add(record.home_team)
                if away_team_id is None:
                    unresolved.add(record.away_team)

                match_row = connection.execute(
                    """
                    INSERT INTO core.historical_matches (
                        competition_code, source_division, season_label, match_date, match_time_local,
                        source_timezone, home_team, away_team, home_team_norm, away_team_norm,
                        home_goals, away_goals, full_time_result, ht_home_goals, ht_away_goals,
                        half_time_result, team_id_home, team_id_away, source_file_id,
                        source_row_number, source_row_hash, match_stats, raw_row, quality_flags
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (source_division, season_label, match_date, home_team_norm, away_team_norm)
                    DO UPDATE SET
                        match_time_local = EXCLUDED.match_time_local,
                        source_timezone = EXCLUDED.source_timezone,
                        home_team = EXCLUDED.home_team,
                        away_team = EXCLUDED.away_team,
                        home_goals = EXCLUDED.home_goals,
                        away_goals = EXCLUDED.away_goals,
                        full_time_result = EXCLUDED.full_time_result,
                        ht_home_goals = EXCLUDED.ht_home_goals,
                        ht_away_goals = EXCLUDED.ht_away_goals,
                        half_time_result = EXCLUDED.half_time_result,
                        team_id_home = COALESCE(EXCLUDED.team_id_home, core.historical_matches.team_id_home),
                        team_id_away = COALESCE(EXCLUDED.team_id_away, core.historical_matches.team_id_away),
                        source_file_id = EXCLUDED.source_file_id,
                        source_row_number = EXCLUDED.source_row_number,
                        source_row_hash = EXCLUDED.source_row_hash,
                        match_stats = EXCLUDED.match_stats,
                        raw_row = EXCLUDED.raw_row,
                        result_status = CASE
                            WHEN core.historical_matches.source_row_hash = EXCLUDED.source_row_hash
                                THEN core.historical_matches.result_status
                            ELSE 'unverified'
                        END,
                        result_verification_source = CASE
                            WHEN core.historical_matches.source_row_hash = EXCLUDED.source_row_hash
                                THEN core.historical_matches.result_verification_source
                            ELSE NULL
                        END,
                        result_verification_report_sha256 = CASE
                            WHEN core.historical_matches.source_row_hash = EXCLUDED.source_row_hash
                                THEN core.historical_matches.result_verification_report_sha256
                            ELSE NULL
                        END,
                        result_verified_at_utc = CASE
                            WHEN core.historical_matches.source_row_hash = EXCLUDED.source_row_hash
                                THEN core.historical_matches.result_verified_at_utc
                            ELSE NULL
                        END,
                        quality_flags = CASE
                            WHEN core.historical_matches.source_row_hash = EXCLUDED.source_row_hash
                                THEN (
                                    SELECT COALESCE(jsonb_agg(flag ORDER BY flag), '[]'::jsonb)
                                    FROM (
                                        SELECT DISTINCT flag
                                        FROM jsonb_array_elements_text(
                                            core.historical_matches.quality_flags
                                            || EXCLUDED.quality_flags
                                        ) AS merged(flag)
                                    ) AS unique_flags
                                )
                            ELSE EXCLUDED.quality_flags
                        END,
                        imported_at_utc = now()
                    RETURNING hist_match_id
                    """,
                    (
                        record.competition_code,
                        record.source_division,
                        record.season_label,
                        record.match_date,
                        record.match_time_local,
                        record.source_timezone,
                        record.home_team,
                        record.away_team,
                        record.home_team_norm,
                        record.away_team_norm,
                        record.home_goals,
                        record.away_goals,
                        record.full_time_result,
                        record.ht_home_goals,
                        record.ht_away_goals,
                        record.half_time_result,
                        home_team_id,
                        away_team_id,
                        source_file_id,
                        record.source_row_number,
                        record.source_row_hash,
                        Jsonb(record.match_stats),
                        Jsonb(record.raw_row),
                        Jsonb(list(record.quality_flags)),
                    ),
                ).fetchone()
                hist_match_id = int(match_row["hist_match_id"])
                imported_match_ids.append(hist_match_id)
                imported_partition_ids.setdefault(
                    (record.source_division, record.season_label),
                    set(),
                ).add(hist_match_id)
                odds_rows.extend(
                    (
                        hist_match_id,
                        quote.bookmaker,
                        quote.market_type,
                        quote.is_closing,
                        quote.price_timing,
                        quote.point_in_time_safe,
                        quote.line_value,
                        quote.odd_home,
                        quote.odd_draw,
                        quote.odd_away,
                        quote.odd_over,
                        quote.odd_under,
                        Jsonb(quote.source_columns),
                        Jsonb(list(quote.quality_flags)),
                    )
                    for quote in record.odds
                )

            # The latest validated file is authoritative for its division/season.
            # Remove matches that disappeared from a revised source file instead
            # of silently retaining records from an older file version.
            stale_match_ids: list[int] = []
            for (source_division, season_label), partition_ids in imported_partition_ids.items():
                existing_ids = {
                    int(row["hist_match_id"])
                    for row in connection.execute(
                        """
                        SELECT hist_match_id
                        FROM core.historical_matches
                        WHERE source_division = %s AND season_label = %s
                        """,
                        (source_division, season_label),
                    ).fetchall()
                }
                stale_match_ids.extend(sorted(existing_ids - partition_ids))
            if stale_match_ids:
                connection.execute(
                    """
                    DELETE FROM core.historical_matches
                    WHERE hist_match_id = ANY(%s::bigint[])
                    """,
                    (stale_match_ids,),
                )

            # Replace the normalized quote set for every imported match. Without
            # this step, a value removed or invalidated in a revised CSV would
            # leave a stale quote behind from the previous import.
            if imported_match_ids:
                connection.execute(
                    """
                    DELETE FROM core.historical_odds
                    WHERE hist_match_id = ANY(%s::bigint[])
                    """,
                    (imported_match_ids,),
                )

            if odds_rows:
                connection.cursor().executemany(
                    """
                    INSERT INTO core.historical_odds (
                        hist_match_id, bookmaker, market_type, is_closing,
                        price_timing, point_in_time_safe, line_value,
                        odd_home, odd_draw, odd_away, odd_over, odd_under,
                        source_columns, quality_flags
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (hist_match_id, bookmaker, market_type, is_closing)
                    DO UPDATE SET
                        price_timing = EXCLUDED.price_timing,
                        point_in_time_safe = EXCLUDED.point_in_time_safe,
                        line_value = EXCLUDED.line_value,
                        odd_home = EXCLUDED.odd_home,
                        odd_draw = EXCLUDED.odd_draw,
                        odd_away = EXCLUDED.odd_away,
                        odd_over = EXCLUDED.odd_over,
                        odd_under = EXCLUDED.odd_under,
                        source_columns = EXCLUDED.source_columns,
                        quality_flags = EXCLUDED.quality_flags,
                        imported_at_utc = now()
                    """,
                    odds_rows,
                )

            connection.execute(
                """
                UPDATE raw.source_files
                SET status = 'imported', row_count = %s, validation_report = %s
                WHERE source_file_id = %s
                """,
                (len(parsed.records), Jsonb(parsed.validation_report), source_file_id),
            )
            connection.commit()

        return {
            "source_file_id": source_file_id,
            "matches_upserted": len(parsed.records),
            "odds_upserted": len(odds_rows),
            "stale_matches_deleted": len(stale_match_ids),
            "unmapped_team_count": len(unresolved),
            "unmapped_teams": sorted(unresolved),
        }

    @staticmethod
    def _upsert_source_file(connection: Connection, parsed: ParsedHistoricalFile) -> int:
        downloaded = parsed.downloaded
        spec = downloaded.spec
        row = connection.execute(
            """
            INSERT INTO raw.source_files (
                source_name, source_url, source_division, season_label, http_etag,
                http_last_modified, content_sha256, content_bytes, storage_path,
                parser_version, row_count, status, validation_report
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'validated', %s)
            ON CONFLICT (source_name, source_url, content_sha256)
            DO UPDATE SET
                downloaded_at_utc = CASE
                    WHEN %s THEN now()
                    ELSE raw.source_files.downloaded_at_utc
                END,
                http_etag = COALESCE(EXCLUDED.http_etag, raw.source_files.http_etag),
                http_last_modified = COALESCE(
                    EXCLUDED.http_last_modified,
                    raw.source_files.http_last_modified
                ),
                content_bytes = EXCLUDED.content_bytes,
                storage_path = EXCLUDED.storage_path,
                parser_version = EXCLUDED.parser_version,
                row_count = EXCLUDED.row_count,
                status = 'validated',
                validation_report = EXCLUDED.validation_report
            RETURNING source_file_id
            """,
            (
                SOURCE_NAME,
                spec.url,
                spec.league.source_division,
                spec.season.label,
                downloaded.http_etag,
                downloaded.http_last_modified,
                downloaded.content_sha256,
                downloaded.content_bytes,
                str(downloaded.path),
                parsed.validation_report["parser_version"],
                len(parsed.records),
                Jsonb(parsed.validation_report),
                downloaded.retrieved_from_network,
            ),
        ).fetchone()
        return int(row["source_file_id"])

    @staticmethod
    def _ensure_team_map(
        connection: Connection,
        parsed: ParsedHistoricalFile,
        source_file_id: int,
    ) -> dict[str, int]:
        source_names = {
            record.home_team_norm: record.home_team
            for record in parsed.records
        }
        source_names.update({
            record.away_team_norm: record.away_team
            for record in parsed.records
        })
        result: dict[str, int] = {}
        for normalized_name, source_name in sorted(source_names.items()):
            row = connection.execute(
                """
                INSERT INTO core.teams (canonical_name, normalized_name, country_code)
                VALUES (%s, %s, %s)
                ON CONFLICT (normalized_name, country_code)
                DO UPDATE SET active = true
                RETURNING team_id
                """,
                (
                    source_name,
                    normalized_name,
                    parsed.downloaded.spec.league.country_code,
                ),
            ).fetchone()
            team_id = int(row["team_id"])
            result[normalized_name] = team_id
            connection.execute(
                """
                INSERT INTO core.team_aliases (
                    team_id, source_name, competition_code, source_alias,
                    normalized_alias, verified, verification_method,
                    evidence, verified_at_utc
                )
                VALUES (
                    %s, 'football-data.co.uk', %s, %s, %s, true,
                    'exact value from hash-pinned source CSV', %s, now()
                )
                ON CONFLICT (source_name, competition_code, normalized_alias)
                DO UPDATE SET
                    team_id = EXCLUDED.team_id,
                    source_alias = EXCLUDED.source_alias,
                    verified = true,
                    verification_method = EXCLUDED.verification_method,
                    evidence = EXCLUDED.evidence,
                    verified_at_utc = now()
                """,
                (
                    team_id,
                    parsed.downloaded.spec.league.competition_code,
                    source_name,
                    normalized_name,
                    Jsonb({
                        "source_file_id": source_file_id,
                        "source_sha256": parsed.downloaded.content_sha256,
                        "season": parsed.downloaded.spec.season.label,
                    }),
                ),
            )
        return result

    def stats(self) -> dict[str, Any]:
        with connect() as connection:
            totals = connection.execute(
                """
                SELECT
                    (SELECT count(*) FROM raw.source_files
                     WHERE source_name = 'football-data.co.uk' AND status = 'imported') AS source_files,
                    (SELECT count(*) FROM core.historical_matches) AS matches,
                    (SELECT count(*) FROM core.historical_odds) AS odds,
                    (SELECT count(*) FROM core.historical_odds
                     WHERE price_timing = 'preclosing_legacy') AS legacy_timing_odds,
                    (SELECT count(*) FROM core.historical_odds
                     WHERE price_timing = 'opening') AS opening_odds,
                    (SELECT count(*) FROM core.historical_odds
                     WHERE price_timing = 'closing') AS closing_odds,
                    (SELECT count(*) FROM core.historical_odds
                     WHERE point_in_time_safe) AS point_in_time_safe_odds,
                    (SELECT count(*) FROM core.historical_matches
                     WHERE result_status <> 'unverified') AS verified_matches,
                    (SELECT count(*) FROM core.historical_matches
                     WHERE result_status = 'unverified') AS unverified_matches,
                    (SELECT count(*) FROM core.historical_matches
                     WHERE result_status = 'awarded') AS awarded_matches,
                    (SELECT count(*) FROM core.team_aliases
                     WHERE verified) AS verified_team_aliases,
                    (SELECT count(*) FROM audit.historical_validation_reports
                     WHERE status IN ('pass', 'pass_with_warnings')) AS validation_reports,
                    (SELECT COALESCE(sum(
                        COALESCE((validation_report->>'invalid_odds_cell_count')::integer, 0)
                     ), 0)
                     FROM raw.source_files
                     WHERE source_name = 'football-data.co.uk'
                       AND status = 'imported') AS excluded_invalid_odds_cells,
                    (SELECT COALESCE(sum(
                        COALESCE((validation_report->>'pinnacle_quote_groups_quarantined')::integer, 0)
                     ), 0)
                     FROM raw.source_files
                     WHERE source_name = 'football-data.co.uk'
                       AND status = 'imported') AS quarantined_pinnacle_quote_groups,
                    (SELECT COALESCE(sum(
                        COALESCE(
                            (validation_report->>'market_consistency_quote_groups_quarantined')::integer,
                            0
                        )
                     ), 0)
                     FROM raw.source_files
                     WHERE source_name = 'football-data.co.uk'
                       AND status = 'imported') AS quarantined_market_consistency_quote_groups,
                    (SELECT count(*) FROM core.historical_matches
                     WHERE source_timezone = 'unknown') AS timezone_unknown_matches,
                    (SELECT min(match_date) FROM core.historical_matches) AS first_match_date,
                    (SELECT max(match_date) FROM core.historical_matches) AS last_match_date,
                    (SELECT count(DISTINCT season_label) FROM core.historical_matches) AS seasons,
                    (SELECT count(DISTINCT season_label) FROM core.historical_matches
                     WHERE source_division IN ('E0','SP1','D1','I1','F1')) AS big_five_seasons,
                    (SELECT count(DISTINCT season_label) FROM core.historical_matches
                     WHERE source_division IN ('FIN','NOR','SWE')) AS nordic_calendar_seasons,
                    (SELECT count(DISTINCT competition_code) FROM core.historical_matches) AS competitions
                """
            ).fetchone()
            coverage = connection.execute(
                """
                SELECT competition_code, season_label, count(*) AS matches,
                       min(match_date) AS first_match_date, max(match_date) AS last_match_date,
                       count(DISTINCT home_team_norm) AS home_teams,
                       count(*) FILTER (WHERE result_status <> 'unverified') AS verified_matches,
                       count(*) FILTER (WHERE result_status = 'awarded') AS awarded_matches
                FROM core.historical_matches
                GROUP BY competition_code, season_label
                ORDER BY season_label, competition_code
                """
            ).fetchall()
            latest_runs = connection.execute(
                """
                SELECT import_run_id, started_at_utc, finished_at_utc, requested_files,
                       imported_files, matches_upserted, odds_upserted, status, error_summary
                FROM raw.historical_import_runs
                ORDER BY import_run_id DESC
                LIMIT 10
                """
            ).fetchall()
            latest_validation_reports = connection.execute(
                """
                SELECT report_sha256, primary_source, verification_sources, season_label,
                       matches_checked, status, generated_at_utc, applied_at_utc
                FROM audit.historical_validation_reports
                ORDER BY applied_at_utc DESC
                LIMIT 10
                """
            ).fetchall()
        return {
            "totals": totals,
            "coverage": coverage,
            "latest_runs": latest_runs,
            "latest_validation_reports": latest_validation_reports,
        }


historical_repository = HistoricalRepository()
