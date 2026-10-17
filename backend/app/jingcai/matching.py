"""Conservative linkage between official Jingcai and consensus events.

Provider event identifiers are stable, while translated team names are not.  A
link is accepted only when an exact team alias identifies the event or when the
league and kickoff window contain exactly one candidate.  Ambiguous kickoff
groups are deliberately left unmatched instead of guessing from similar odds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from .consensus import SPORT_KEY_BY_LEAGUE
from .team_names import consensus_names

LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
KICKOFF_TOLERANCE = timedelta(minutes=20)


@dataclass(frozen=True)
class ConsensusEventMatch:
    provider_event_id: str
    home_team: str
    away_team: str
    commence_time_utc: datetime
    method: str
    confidence: float


def jingcai_kickoff_utc(
    match_date_local: date | None,
    match_time_local: str | time | None,
) -> datetime | None:
    if match_date_local is None or match_time_local in (None, ""):
        return None
    try:
        parsed_time = (
            match_time_local
            if isinstance(match_time_local, time)
            else time.fromisoformat(str(match_time_local))
        )
    except ValueError:
        return None
    local = datetime.combine(match_date_local, parsed_time).replace(tzinfo=LOCAL_TIMEZONE)
    return local.astimezone(UTC)


def choose_consensus_event(
    candidates: Iterable[Mapping[str, Any]],
    *,
    home_team: str,
    away_team: str,
) -> ConsensusEventMatch | None:
    options = list(candidates)
    if not options:
        return None

    home_en = consensus_names(home_team)
    away_en = consensus_names(away_team)
    if home_en and away_en:
        exact = [
            row
            for row in options
            if row["home_team"] in home_en and row["away_team"] in away_en
        ]
        if len(exact) == 1:
            return _as_match(exact[0], "team_alias_pair", 1.0)

    if home_en or away_en:
        partial = [
            row
            for row in options
            if (not home_en or row["home_team"] in home_en)
            and (not away_en or row["away_team"] in away_en)
        ]
        if len(partial) == 1:
            return _as_match(partial[0], "team_alias_single", 0.99)

    if len(options) == 1 and not home_en and not away_en:
        return _as_match(options[0], "league_kickoff_unique", 0.98)
    return None


def resolve_consensus_event(
    connection: Any,
    *,
    league_name: str | None,
    home_team: str,
    away_team: str,
    match_date_local: date | None,
    match_time_local: str | time | None,
) -> ConsensusEventMatch | None:
    kickoff = jingcai_kickoff_utc(match_date_local, match_time_local)
    sport_key = SPORT_KEY_BY_LEAGUE.get(league_name or "")
    candidates: list[Mapping[str, Any]] = []
    if kickoff is not None and sport_key:
        candidates = list(
            connection.execute(
                """
                SELECT DISTINCT ON (provider_event_id)
                       provider_event_id, home_team, away_team, commence_time_utc
                FROM jingcai.consensus_odds_snapshots
                WHERE sport_key = %s
                  AND commence_time_utc BETWEEN %s AND %s
                ORDER BY provider_event_id, captured_at_utc DESC,
                         consensus_snapshot_id DESC
                """,
                (sport_key, kickoff - KICKOFF_TOLERANCE, kickoff + KICKOFF_TOLERANCE),
            ).fetchall()
        )
    selected = choose_consensus_event(
        candidates,
        home_team=home_team,
        away_team=away_team,
    )
    if selected is not None and selected.method == "league_kickoff_unique":
        # Reciprocal uniqueness matters: if several Jingcai fixtures share the
        # same league/kickoff but the provider exposes only one of them, linking
        # every local fixture to that sole provider event would be a silent and
        # serious corruption.
        local_group = connection.execute(
            """
            SELECT count(*) AS matches
            FROM jingcai.jc_matches
            WHERE league_name = %s
              AND match_date_local = %s
              AND match_time_local = %s
            """,
            (league_name, match_date_local, str(match_time_local)),
        ).fetchone()
        if not local_group or int(local_group["matches"]) != 1:
            selected = None
    if selected is not None:
        return selected

    # Some cup competitions do not have a sport-key mapping. Exact aliases can
    # still link them safely; constrain by kickoff whenever it is available so
    # a later rematch cannot be selected accidentally.
    home_en = consensus_names(home_team)
    away_en = consensus_names(away_team)
    if sport_key or kickoff is None or not home_en or not away_en:
        return None
    params: list[Any] = [sorted(home_en), sorted(away_en)]
    time_clause = ""
    if kickoff is not None:
        time_clause = "AND commence_time_utc BETWEEN %s AND %s"
        params.extend([kickoff - KICKOFF_TOLERANCE, kickoff + KICKOFF_TOLERANCE])
    rows = connection.execute(
        f"""
        SELECT DISTINCT ON (provider_event_id)
               provider_event_id, home_team, away_team, commence_time_utc
        FROM jingcai.consensus_odds_snapshots
        WHERE home_team = ANY(%s) AND away_team = ANY(%s)
          {time_clause}
        ORDER BY provider_event_id, captured_at_utc DESC,
                 consensus_snapshot_id DESC
        LIMIT 2
        """,
        params,
    ).fetchall()
    return _as_match(rows[0], "team_alias_pair", 1.0) if len(rows) == 1 else None


def _as_match(
    row: Mapping[str, Any],
    method: str,
    confidence: float,
) -> ConsensusEventMatch:
    return ConsensusEventMatch(
        provider_event_id=str(row["provider_event_id"]),
        home_team=str(row["home_team"]),
        away_team=str(row["away_team"]),
        commence_time_utc=row["commence_time_utc"],
        method=method,
        confidence=confidence,
    )
