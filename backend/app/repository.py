from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from .database import connect
from .localization import canonical_competition_code, country_code


COUNTRY_NAMES_BY_CODE = {
    "GB": "英格兰", "ES": "西班牙", "DE": "德国", "IT": "意大利", "FR": "法国",
    "SE": "瑞典", "NO": "挪威", "FI": "芬兰",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def _normalized_name(value: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", ascii_name) or re.sub(r"\W+", "", value.lower())


def _parse_datetime(value: str | None, fallback: datetime | None = None) -> datetime:
    if not value:
        return fallback or utc_now()
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _season_dates(item: dict[str, Any]) -> tuple[str, date, date]:
    label = str(item.get("season_label") or "unknown")
    start_raw = item.get("season_start")
    end_raw = item.get("season_end")
    if start_raw and end_raw:
        return label, date.fromisoformat(start_raw[:10]), date.fromisoformat(end_raw[:10])
    try:
        year = int(label[:4])
    except (TypeError, ValueError):
        year = _parse_datetime(item["kickoff_utc"]).year
        label = str(year)
    if item.get("country_original") in {"Sweden", "Norway", "Finland"}:
        return label, date(year, 1, 1), date(year, 12, 31)
    return f"{year}/{str(year + 1)[-2:]}", date(year, 7, 1), date(year + 1, 6, 30)


def _matchday(round_value: str | None) -> int | None:
    if not round_value:
        return None
    match = re.search(r"(\d+)$", round_value)
    return int(match.group(1)) if match else None


class Repository:
    def start_batch(self, source_name: str, resource_type: str) -> int:
        with connect() as connection:
            row = connection.execute(
                """
                INSERT INTO raw.ingestion_batches (source_name, resource_type, status)
                VALUES (%s, %s, 'running') RETURNING batch_id
                """,
                (source_name, resource_type),
            ).fetchone()
            connection.commit()
        return int(row["batch_id"])

    def finish_batch(self, batch_id: int, *, request_count: int, record_count: int, status: str = "succeeded") -> None:
        with connect() as connection:
            connection.execute(
                """
                UPDATE raw.ingestion_batches
                SET finished_at_utc = now(), request_count = %s, record_count = %s, status = %s
                WHERE batch_id = %s
                """,
                (request_count, record_count, status, batch_id),
            )
            connection.commit()

    def fail_batch(self, batch_id: int, error: Exception, *, request_count: int = 0) -> None:
        with connect() as connection:
            connection.execute(
                """
                UPDATE raw.ingestion_batches
                SET finished_at_utc = now(), request_count = %s, status = 'failed', error_summary = %s
                WHERE batch_id = %s
                """,
                (request_count, str(error)[:1000], batch_id),
            )
            connection.commit()

    def store_raw_payload(
        self,
        connection: Connection,
        *,
        batch_id: int,
        source_name: str,
        resource_type: str,
        payload: Any,
        source_entity_id: str | None = None,
        event_time_utc: datetime | None = None,
    ) -> bool:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        row = connection.execute(
            """
            INSERT INTO raw.source_payloads
                (batch_id, source_name, source_entity_id, resource_type, event_time_utc,
                 source_timezone, payload_hash, payload)
            VALUES (%s, %s, %s, %s, %s, 'UTC', %s, %s)
            ON CONFLICT (source_name, resource_type, payload_hash) DO NOTHING
            RETURNING payload_id
            """,
            (batch_id, source_name, source_entity_id, resource_type, event_time_utc, payload_hash, Jsonb(payload)),
        ).fetchone()
        return row is not None

    @staticmethod
    def _upsert_mapping(connection: Connection, entity_type: str, canonical_id: int, source: str, source_id: str) -> None:
        connection.execute(
            """
            INSERT INTO core.source_entity_mappings
                (entity_type, canonical_id, source_name, source_entity_id, confidence, verified_at_utc)
            VALUES (%s, %s, %s, %s, 1, now())
            ON CONFLICT (entity_type, source_name, source_entity_id)
            DO UPDATE SET canonical_id = EXCLUDED.canonical_id, confidence = 1, verified_at_utc = now()
            """,
            (entity_type, canonical_id, source, source_id),
        )

    @staticmethod
    def _mapped_id(connection: Connection, entity_type: str, source: str, source_id: str) -> int | None:
        row = connection.execute(
            """
            SELECT canonical_id FROM core.source_entity_mappings
            WHERE entity_type = %s AND source_name = %s AND source_entity_id = %s
            """,
            (entity_type, source, source_id),
        ).fetchone()
        return int(row["canonical_id"]) if row else None

    def _upsert_team(self, connection: Connection, team: dict[str, Any], source: str, country: str | None) -> int:
        source_id = str(team.get("id") or "")
        mapped = self._mapped_id(connection, "team", source, source_id) if source_id else None
        name = team.get("name") or team.get("name_original") or "Unknown team"
        normalized = _normalized_name(team.get("name_original") or name)
        code = country_code(country)
        if not mapped:
            alias_row = connection.execute(
                """
                SELECT min(team_id) AS team_id, count(DISTINCT team_id) AS team_count
                FROM core.team_aliases
                WHERE source_name = %s AND normalized_alias = %s
                """,
                (source, normalized),
            ).fetchone()
            if alias_row and int(alias_row["team_count"] or 0) == 1:
                mapped = int(alias_row["team_id"])
                if source_id:
                    self._upsert_mapping(connection, "team", mapped, source, source_id)
        if mapped:
            connection.execute("UPDATE core.teams SET active = true WHERE team_id = %s", (mapped,))
            return mapped
        row = connection.execute(
            """
            INSERT INTO core.teams (canonical_name, normalized_name, country_code)
            VALUES (%s, %s, %s)
            ON CONFLICT (normalized_name, country_code)
            DO UPDATE SET canonical_name = EXCLUDED.canonical_name, active = true
            RETURNING team_id
            """,
            (name, normalized, code),
        ).fetchone()
        team_id = int(row["team_id"])
        if source_id:
            self._upsert_mapping(connection, "team", team_id, source, source_id)
        return team_id

    def _upsert_player(self, connection: Connection, player: dict[str, Any], source: str) -> int:
        source_id = str(player.get("id") or "")
        mapped = self._mapped_id(connection, "player", source, source_id) if source_id else None
        original = player.get("name") or "Unknown player"
        name = original
        normalized = _normalized_name(original)
        if mapped:
            connection.execute(
                "UPDATE core.players SET canonical_name = %s, normalized_name = %s, active = true WHERE player_id = %s",
                (name, normalized, mapped),
            )
            return mapped
        row = connection.execute(
            """
            INSERT INTO core.players (canonical_name, normalized_name)
            VALUES (%s, %s) RETURNING player_id
            """,
            (name, normalized),
        ).fetchone()
        player_id = int(row["player_id"])
        if source_id:
            self._upsert_mapping(connection, "player", player_id, source, source_id)
        return player_id

    def upsert_fixture(self, connection: Connection, item: dict[str, Any], source: str) -> int:
        competition_name = item["competition"]
        original_competition = item.get("competition_original") or competition_name
        original_country = item.get("country_original")
        competition_code = canonical_competition_code(original_country, original_competition)
        competition_row = connection.execute(
            """
            INSERT INTO core.competitions (code, name, country_code, tier, active)
            VALUES (%s, %s, %s, 1, true)
            ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, active = true
            RETURNING competition_id
            """,
            (competition_code, competition_name, country_code(original_country)),
        ).fetchone()
        competition_id = int(competition_row["competition_id"])
        source_competition_id = str(item.get("competition_code") or "")
        if source_competition_id:
            self._upsert_mapping(connection, "competition", competition_id, source, source_competition_id)

        season_label, starts_on, ends_on = _season_dates(item)
        season_row = connection.execute(
            """
            INSERT INTO core.seasons (competition_id, label, starts_on, ends_on)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (competition_id, label)
            DO UPDATE SET starts_on = EXCLUDED.starts_on, ends_on = EXCLUDED.ends_on
            RETURNING season_id
            """,
            (competition_id, season_label, starts_on, ends_on),
        ).fetchone()
        season_id = int(season_row["season_id"])

        home_team_id = self._upsert_team(connection, item["home_team"], source, original_country)
        away_team_id = self._upsert_team(connection, item["away_team"], source, original_country)
        kickoff = _parse_datetime(item["kickoff_utc"])
        score = item.get("score") or {}
        match_row = connection.execute(
            """
            INSERT INTO core.matches
                (season_id, home_team_id, away_team_id, kickoff_utc, venue, matchday, status,
                 home_goals, away_goals, last_source_update_utc)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (season_id, home_team_id, away_team_id, kickoff_utc)
            DO UPDATE SET venue = COALESCE(EXCLUDED.venue, core.matches.venue),
                          matchday = COALESCE(EXCLUDED.matchday, core.matches.matchday),
                          status = EXCLUDED.status,
                          home_goals = EXCLUDED.home_goals,
                          away_goals = EXCLUDED.away_goals,
                          last_source_update_utc = now(),
                          updated_at_utc = now()
            RETURNING match_id
            """,
            (
                season_id, home_team_id, away_team_id, kickoff, item.get("venue"),
                _matchday(item.get("round_original")), item["status"], score.get("home"), score.get("away"),
            ),
        ).fetchone()
        match_id = int(match_row["match_id"])
        source_match_id = str(item.get("provider_ids", {}).get(source) or item.get("fixture_id", "")).removeprefix("af-").removeprefix("fd-")
        if source_match_id:
            self._upsert_mapping(connection, "match", match_id, source, source_match_id)
        return match_id

    def persist_fixture_response(
        self,
        *,
        batch_id: int,
        source: str,
        payload: dict[str, Any],
        fixtures: list[dict[str, Any]],
        source_entity_id: str,
    ) -> dict[str, int]:
        with connect() as connection:
            raw_inserted = int(self.store_raw_payload(
                connection,
                batch_id=batch_id,
                source_name=source,
                resource_type="fixtures",
                payload=payload,
                source_entity_id=source_entity_id,
            ))
            match_ids = [self.upsert_fixture(connection, item, source) for item in fixtures]
            connection.commit()
        return {"raw_inserted": raw_inserted, "fixtures_upserted": len(match_ids)}

    def _match_id(self, connection: Connection, source_fixture_id: Any) -> int | None:
        return self._mapped_id(connection, "match", "api-football", str(source_fixture_id))

    def persist_injuries(self, *, batch_id: int, payload: dict[str, Any], captured_at: datetime) -> dict[str, int]:
        records = payload.get("response") or []
        inserted = 0
        with connect() as connection:
            raw_inserted = int(self.store_raw_payload(
                connection, batch_id=batch_id, source_name="api-football", resource_type="injuries",
                payload=payload, source_entity_id="batch", event_time_utc=captured_at,
            ))
            for item in records:
                fixture = item.get("fixture") or {}
                match_id = self._match_id(connection, fixture.get("id"))
                if not match_id:
                    continue
                team = item.get("team") or {}
                player = item.get("player") or {}
                league = item.get("league") or {}
                team_id = self._upsert_team(connection, {**team, "name_original": team.get("name")}, "api-football", league.get("country"))
                player_id = self._upsert_player(connection, player, "api-football")
                reason = str(player.get("reason") or item.get("reason") or "unknown")
                reason_lower = reason.lower()
                status = "suspended" if "suspend" in reason_lower else "doubtful" if "doubt" in reason_lower else "out"
                observed_on = _parse_datetime(fixture.get("date"), captured_at).date()
                connection.execute(
                    """
                    INSERT INTO core.injuries
                        (player_id, team_id, injury_type, status, starts_on, source_name, captured_at_utc, source_payload)
                    VALUES (%s, %s, %s, %s, %s, 'api-football', %s, %s)
                    """,
                    (player_id, team_id, reason, status, observed_on, captured_at, Jsonb(item)),
                )
                inserted += 1
            connection.commit()
        return {"raw_inserted": raw_inserted, "injuries_inserted": inserted}

    def persist_odds(self, *, batch_id: int, payload: dict[str, Any], captured_at: datetime) -> dict[str, int]:
        inserted = 0
        with connect() as connection:
            raw_inserted = int(self.store_raw_payload(
                connection, batch_id=batch_id, source_name="api-football", resource_type="odds",
                payload=payload, source_entity_id="batch", event_time_utc=captured_at,
            ))
            for fixture_market in payload.get("response") or []:
                fixture = fixture_market.get("fixture") or {}
                match_id = self._match_id(connection, fixture.get("id"))
                if not match_id:
                    continue
                snapshot_at = _parse_datetime(fixture_market.get("update"), captured_at)
                for bookmaker in fixture_market.get("bookmakers") or []:
                    for bet in bookmaker.get("bets") or []:
                        if bet.get("id") != 1 and str(bet.get("name", "")).lower() not in {"match winner", "1x2"}:
                            continue
                        for value in bet.get("values") or []:
                            try:
                                decimal = Decimal(str(value.get("odd")))
                            except (InvalidOperation, TypeError):
                                continue
                            if decimal <= 1:
                                continue
                            row = connection.execute(
                                """
                                INSERT INTO core.odds_snapshots
                                    (match_id, source_name, bookmaker, market_type, selection_key,
                                     odds_decimal, captured_at_utc, snapshot_type, raw_market)
                                VALUES (%s, 'api-football', %s, '1X2', %s, %s, %s, 'preclose', %s)
                                ON CONFLICT (match_id, source_name, bookmaker, market_type, selection_key, captured_at_utc)
                                DO NOTHING RETURNING odds_snapshot_id
                                """,
                                (match_id, bookmaker.get("name") or str(bookmaker.get("id")), str(value.get("value")).lower(), decimal, snapshot_at, Jsonb({"bet": bet, "bookmaker_id": bookmaker.get("id")})),
                            ).fetchone()
                            inserted += int(row is not None)
            connection.commit()
        return {"raw_inserted": raw_inserted, "odds_inserted": inserted}

    def persist_fixture_bundle(self, *, batch_id: int, payload: dict[str, Any], captured_at: datetime) -> dict[str, int]:
        counts = {"events": 0, "lineups": 0, "player_stats": 0}
        with connect() as connection:
            raw_inserted = int(self.store_raw_payload(
                connection, batch_id=batch_id, source_name="api-football", resource_type="fixture_bundle",
                payload=payload, source_entity_id="batch", event_time_utc=captured_at,
            ))
            for fixture_data in payload.get("response") or []:
                fixture = fixture_data.get("fixture") or {}
                fixture_id = fixture.get("id")
                match_id = self._match_id(connection, fixture_id)
                if not match_id:
                    continue
                league = fixture_data.get("league") or {}
                country = league.get("country")
                kickoff = _parse_datetime(fixture.get("date"), captured_at)
                for index, event in enumerate(fixture_data.get("events") or []):
                    team = event.get("team") or {}
                    player = event.get("player") or {}
                    assist = event.get("assist") or {}
                    team_id = self._upsert_team(connection, {**team, "name_original": team.get("name")}, "api-football", country) if team.get("id") else None
                    player_id = self._upsert_player(connection, player, "api-football") if player.get("id") else None
                    related_id = self._upsert_player(connection, assist, "api-football") if assist.get("id") else None
                    elapsed = int((event.get("time") or {}).get("elapsed") or 0)
                    extra = int((event.get("time") or {}).get("extra") or 0)
                    event_key = hashlib.sha256(json.dumps([fixture_id, index, event], sort_keys=True).encode()).hexdigest()[:32]
                    row = connection.execute(
                        """
                        INSERT INTO core.match_events
                            (match_id, source_name, source_event_id, team_id, player_id, related_player_id,
                             event_type, event_time_utc, minute, qualifiers)
                        VALUES (%s, 'api-football', %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (source_name, source_event_id) DO NOTHING RETURNING event_id
                        """,
                        (match_id, event_key, team_id, player_id, related_id, event.get("type") or "unknown", kickoff + timedelta(minutes=elapsed + extra), min(elapsed + extra, 140), Jsonb(event)),
                    ).fetchone()
                    counts["events"] += int(row is not None)

                for lineup in fixture_data.get("lineups") or []:
                    team = lineup.get("team") or {}
                    team_id = self._upsert_team(connection, {**team, "name_original": team.get("name")}, "api-football", country)
                    for starter, entries in ((True, lineup.get("startXI") or []), (False, lineup.get("substitutes") or [])):
                        for entry in entries:
                            player = entry.get("player") or {}
                            player_id = self._upsert_player(connection, player, "api-football")
                            row = connection.execute(
                                """
                                INSERT INTO core.lineups
                                    (match_id, team_id, player_id, lineup_type, role, formation_slot,
                                     is_starter, confidence, captured_at_utc)
                                VALUES (%s, %s, %s, 'confirmed', %s, %s, %s, 1, %s)
                                ON CONFLICT (match_id, team_id, player_id, lineup_type, captured_at_utc)
                                DO NOTHING RETURNING lineup_id
                                """,
                                (match_id, team_id, player_id, player.get("pos"), player.get("grid"), starter, captured_at),
                            ).fetchone()
                            counts["lineups"] += int(row is not None)

                for team_block in fixture_data.get("players") or []:
                    team = team_block.get("team") or {}
                    team_id = self._upsert_team(connection, {**team, "name_original": team.get("name")}, "api-football", country)
                    for player_entry in team_block.get("players") or []:
                        player = player_entry.get("player") or {}
                        player_id = self._upsert_player(connection, player, "api-football")
                        statistics = (player_entry.get("statistics") or [{}])[0]
                        games = statistics.get("games") or {}
                        goals = statistics.get("goals") or {}
                        shots = statistics.get("shots") or {}
                        passes = statistics.get("passes") or {}
                        tackles = statistics.get("tackles") or {}
                        duels = statistics.get("duels") or {}
                        row = connection.execute(
                            """
                            INSERT INTO core.player_match_stats
                                (match_id, player_id, team_id, position_bucket, minutes_played, started,
                                 goals, assists, shots, shots_on_target, key_passes, tackles,
                                 interceptions, duels_won, saves, raw_metrics)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (match_id, player_id)
                            DO UPDATE SET position_bucket = EXCLUDED.position_bucket,
                                          minutes_played = EXCLUDED.minutes_played,
                                          started = EXCLUDED.started,
                                          goals = EXCLUDED.goals,
                                          assists = EXCLUDED.assists,
                                          shots = EXCLUDED.shots,
                                          shots_on_target = EXCLUDED.shots_on_target,
                                          key_passes = EXCLUDED.key_passes,
                                          tackles = EXCLUDED.tackles,
                                          interceptions = EXCLUDED.interceptions,
                                          duels_won = EXCLUDED.duels_won,
                                          saves = EXCLUDED.saves,
                                          raw_metrics = EXCLUDED.raw_metrics
                            RETURNING player_match_stat_id
                            """,
                            (
                                match_id, player_id, team_id, games.get("position"), int(games.get("minutes") or 0),
                                not bool(games.get("substitute")), int(goals.get("total") or 0), int(goals.get("assists") or 0),
                                shots.get("total"), shots.get("on"), passes.get("key"), tackles.get("total"),
                                tackles.get("interceptions"), duels.get("won"), goals.get("saves"), Jsonb(statistics),
                            ),
                        ).fetchone()
                        counts["player_stats"] += int(row is not None)
            connection.commit()
        return {"raw_inserted": raw_inserted, **counts}

    def stats(self) -> dict[str, Any]:
        with connect() as connection:
            counts = connection.execute(
                """
                SELECT
                  (SELECT count(*) FROM raw.ingestion_batches) AS batches,
                  (SELECT count(*) FROM raw.source_payloads) AS raw_payloads,
                  (SELECT count(*) FROM core.competitions) AS competitions,
                  (SELECT count(*) FROM core.teams) AS teams,
                  (SELECT count(*) FROM core.players) AS players,
                  (SELECT count(*) FROM core.matches) AS matches,
                  (SELECT count(*) FROM core.injuries) AS injury_snapshots,
                  (SELECT count(*) FROM core.odds_snapshots) AS odds_snapshots,
                  (SELECT count(*) FROM core.lineups) AS lineup_snapshots,
                  (SELECT count(*) FROM core.match_events) AS events,
                  (SELECT count(*) FROM core.player_match_stats) AS player_match_stats,
                  (SELECT count(*) FROM core.historical_matches) AS historical_matches,
                  (SELECT count(*) FROM core.historical_odds) AS historical_odds,
                  (SELECT count(*) FROM raw.source_files
                   WHERE source_name = 'football-data.co.uk' AND status = 'imported')
                    AS historical_source_files
                """
            ).fetchone()
            latest = connection.execute(
                """
                SELECT batch_id, source_name, resource_type, started_at_utc, finished_at_utc,
                       request_count, record_count, status, error_summary
                FROM raw.ingestion_batches ORDER BY batch_id DESC LIMIT 20
                """
            ).fetchall()
        return {"counts": counts, "latest_batches": latest}

    def list_fixtures(self, date_from: date, date_to: date, competition: str | None = None) -> list[dict[str, Any]]:
        with connect() as connection:
            rows = connection.execute(
                """
                SELECT m.match_id, m.kickoff_utc, m.venue, m.matchday, m.status,
                       m.home_goals, m.away_goals, m.last_source_update_utc,
                       c.name AS competition, c.code AS competition_code, c.country_code,
                       s.label AS season_label,
                       ht.team_id AS home_team_id, ht.canonical_name AS home_team,
                       at.team_id AS away_team_id, at.canonical_name AS away_team,
                       COALESCE(map.provider_ids, '{}'::jsonb) AS provider_ids
                FROM core.matches m
                JOIN core.seasons s ON s.season_id = m.season_id
                JOIN core.competitions c ON c.competition_id = s.competition_id
                JOIN core.teams ht ON ht.team_id = m.home_team_id
                JOIN core.teams at ON at.team_id = m.away_team_id
                LEFT JOIN LATERAL (
                    SELECT jsonb_object_agg(source_name, source_entity_id) AS provider_ids
                    FROM core.source_entity_mappings sem
                    WHERE sem.entity_type = 'match' AND sem.canonical_id = m.match_id
                ) map ON true
                WHERE m.kickoff_utc >= %s::date
                  AND m.kickoff_utc < (%s::date + interval '1 day')
                  AND (%s::text IS NULL OR c.name ILIKE '%%' || %s::text || '%%')
                ORDER BY m.kickoff_utc, c.name, ht.canonical_name
                """,
                (date_from, date_to, competition, competition),
            ).fetchall()
        items = []
        for row in rows:
            provider_ids = dict(row["provider_ids"] or {})
            if "api-football" in provider_ids:
                fixture_id = f"af-{provider_ids['api-football']}"
            elif "football-data.org" in provider_ids:
                fixture_id = f"fd-{provider_ids['football-data.org']}"
            else:
                fixture_id = f"db-{row['match_id']}"
            source = " + ".join(sorted(provider_ids)) or "postgresql"
            country = COUNTRY_NAMES_BY_CODE.get(row["country_code"], row["country_code"])
            items.append({
                "fixture_id": fixture_id,
                "provider_ids": provider_ids,
                "source": source,
                "competition": row["competition"],
                "competition_original": row["competition"],
                "competition_code": row["competition_code"],
                "competition_logo": None,
                "country": country,
                "country_original": country,
                "round": f"第{row['matchday']}轮" if row["matchday"] else None,
                "round_original": str(row["matchday"]) if row["matchday"] else None,
                "season_label": row["season_label"],
                "kickoff_utc": row["kickoff_utc"],
                "status": row["status"],
                "status_detail": row["status"],
                "elapsed": None,
                "venue": row["venue"],
                "city": None,
                "home_team": {
                    "id": str(row["home_team_id"]), "name": row["home_team"],
                    "name_original": row["home_team"], "short_name": row["home_team"], "crest": None,
                },
                "away_team": {
                    "id": str(row["away_team_id"]), "name": row["away_team"],
                    "name_original": row["away_team"], "short_name": row["away_team"], "crest": None,
                },
                "score": {"home": row["home_goals"], "away": row["away_goals"]},
                "last_source_update_utc": row["last_source_update_utc"],
            })
        return items

    def resource_due(self, source_name: str, resource_type: str, *, minimum_age: timedelta) -> bool:
        with connect() as connection:
            row = connection.execute(
                """
                SELECT finished_at_utc FROM raw.ingestion_batches
                WHERE source_name = %s AND resource_type = %s AND status IN ('succeeded', 'partial')
                ORDER BY finished_at_utc DESC NULLS LAST LIMIT 1
                """,
                (source_name, resource_type),
            ).fetchone()
        if not row or not row["finished_at_utc"]:
            return True
        return row["finished_at_utc"] <= utc_now() - minimum_age


repository = Repository()
