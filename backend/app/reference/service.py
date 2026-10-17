from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from ..database import PROJECT_ROOT, connect
from ..collector_runtime import load_runtime_state
from ..jingcai.matching import resolve_consensus_event
from ..localization import localize_team
from .model import VERSION, due_checkpoint, market_reference, summarize
from .prematch import research_for_fixture

LOCAL = ZoneInfo("Asia/Shanghai")


def install_schema(connection):
    connection.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"), prepare=False)


def fixtures(connection, now, days=30, match_id=None):
    return connection.execute(
        """SELECT m.*, c.code AS competition_code,c.name AS competition,
               ht.canonical_name AS home_team,at.canonical_name AS away_team,
               (SELECT sem.source_entity_id FROM core.source_entity_mappings sem
                WHERE sem.entity_type='match' AND sem.canonical_id=m.match_id
                  AND sem.source_name='sporttery' LIMIT 1) AS jc_official_id
           FROM core.matches m JOIN core.seasons s USING(season_id)
           JOIN core.competitions c USING(competition_id)
           JOIN core.teams ht ON ht.team_id=m.home_team_id
           JOIN core.teams at ON at.team_id=m.away_team_id
           WHERE (%s::bigint IS NULL OR m.match_id=%s)
             AND m.kickoff_utc>%s AND m.kickoff_utc<=%s
             AND m.status='scheduled' ORDER BY m.kickoff_utc,m.match_id LIMIT 300""",
        (match_id, match_id, now, now+timedelta(days=days)),
    ).fetchall()


def recent_form(connection, team_id, now, side=None):
    # Link through canonical IDs only. Different transliterations are never
    # guessed; an unresolved identity remains a visible small-sample warning.
    rows = connection.execute(
        """WITH games AS (
          SELECT m.match_id AS id, (m.kickoff_utc AT TIME ZONE 'Asia/Shanghai')::date AS day,
                 m.home_team_id AS home_id,m.away_team_id AS away_id,
                 COALESCE(m.regulation_home_goals,m.home_goals) AS hg,
                 COALESCE(m.regulation_away_goals,m.away_goals) AS ag,'live' AS source
          FROM core.matches m WHERE (m.home_team_id=%s OR m.away_team_id=%s)
            AND m.status='finished' AND m.kickoff_utc<%s AND m.updated_at_utc<=%s
            AND m.home_goals IS NOT NULL AND m.away_goals IS NOT NULL
            AND (m.result_duration IS NULL OR m.result_duration='REGULAR'
                 OR (m.regulation_home_goals IS NOT NULL AND m.regulation_away_goals IS NOT NULL))
          UNION ALL
          SELECT hist_match_id,match_date,team_id_home,team_id_away,home_goals,away_goals,'historical'
          FROM core.historical_matches WHERE (team_id_home=%s OR team_id_away=%s)
            AND match_date<%s AND imported_at_utc<=%s AND result_status NOT IN ('awarded','abandoned')
        ), unique_games AS (
          SELECT DISTINCT ON(day,home_id,away_id) * FROM games
          WHERE day >= %s ORDER BY day,home_id,away_id,source DESC
        ) SELECT * FROM unique_games WHERE (%s::text IS NULL
           OR (%s='home' AND home_id=%s) OR (%s='away' AND away_id=%s))
          ORDER BY day DESC,id DESC LIMIT 10""",
        (team_id,team_id,now,now,team_id,team_id,now.astimezone(LOCAL).date(),now,
         (now-timedelta(days=365)).date(),side,side,team_id,side,team_id),
    ).fetchall()
    records = []
    for r in rows:
        home = r["home_id"] == team_id
        gf, ga = (r["hg"],r["ag"]) if home else (r["ag"],r["hg"])
        records.append({"date": str(r["day"]), "venue": "home" if home else "away",
                        "goals_for": gf, "goals_against": ga, "result": "W" if gf>ga else "D" if gf==ga else "L",
                        "source": r["source"], "source_id": r["id"]})
    return {"matches": len(records), "wins": sum(r["result"]=="W" for r in records),
            "draws": sum(r["result"]=="D" for r in records), "losses": sum(r["result"]=="L" for r in records),
            "goals_for": sum(r["goals_for"] for r in records), "goals_against": sum(r["goals_against"] for r in records),
            "latest_date": records[0]["date"] if records else None, "records": records}


def squad_context(connection, fixture, now):
    injuries = connection.execute(
        """SELECT DISTINCT ON(i.player_id) p.canonical_name AS player,i.team_id,i.status,
               i.injury_type,i.captured_at_utc,i.source_name
           FROM core.injuries i JOIN core.players p USING(player_id)
           WHERE i.team_id=ANY(%s) AND i.captured_at_utc BETWEEN %s AND %s
             AND i.starts_on<=%s ORDER BY i.player_id,i.captured_at_utc DESC""",
        ([fixture["home_team_id"],fixture["away_team_id"]],now-timedelta(hours=24),now,now.astimezone(LOCAL).date()),
    ).fetchall()
    lineup = connection.execute(
        """SELECT l.team_id,p.canonical_name AS player,l.captured_at_utc
           FROM core.lineups l JOIN core.players p USING(player_id)
           WHERE l.match_id=%s AND l.lineup_type='confirmed' AND l.is_starter
             AND l.captured_at_utc BETWEEN %s AND %s
             AND l.captured_at_utc=(SELECT max(x.captured_at_utc) FROM core.lineups x
                 WHERE x.match_id=l.match_id AND x.team_id=l.team_id AND x.lineup_type='confirmed'
                   AND x.captured_at_utc<=%s) ORDER BY l.team_id,p.canonical_name""",
        (fixture["match_id"],now-timedelta(hours=6),now,now),
    ).fetchall()
    return {"injuries": injuries, "injury_status": "partial_provider_records" if injuries else "not_available",
            "lineups": lineup, "lineup_status": "confirmed" if all(sum(x["team_id"]==fixture[f"{side}_team_id"] for x in lineup)==11 for side in ("home","away")) else "incomplete"}


def build_card(connection, fixture, now, phase="live"):
    local = fixture["kickoff_utc"].astimezone(LOCAL)
    event = resolve_consensus_event(connection, league_name=fixture["competition"],
        home_team=fixture["home_team"],away_team=fixture["away_team"],
        match_date_local=local.date(),match_time_local=local.time().replace(tzinfo=None))
    # The prospective ledger requires both identities, even where the older
    # research matcher permits a single-team or unique-kickoff association.
    if event and (event.method != 'team_alias_pair' or event.commence_time_utc != fixture['kickoff_utc']):
        event = None
    quotes = []
    if event:
        quotes = connection.execute(
            """SELECT DISTINCT ON(s.bookmaker) s.consensus_snapshot_id AS snapshot_id,s.bookmaker,
                 s.provider,s.provider_event_id,s.odd_home::float8,s.odd_draw::float8,s.odd_away::float8,
                 observed.t AS observed_at_utc
               FROM jingcai.consensus_odds_snapshots s
               JOIN LATERAL (SELECT max(t) AS t FROM unnest(
                 ARRAY[s.captured_at_utc,s.last_observed_at_utc]||s.observation_times_utc) times(t)
                 WHERE t<=%s) observed ON true
               WHERE s.provider_event_id=%s AND s.market_type='1X2' AND s.point_in_time_safe
                 AND observed.t IS NOT NULL ORDER BY s.bookmaker,observed.t DESC,s.consensus_snapshot_id DESC""",
            (now,event.provider_event_id),
        ).fetchall()
    market = market_reference(quotes,now,fixture["kickoff_utc"],phase)
    squads = squad_context(connection,fixture,now)
    forms = {side: {"overall": recent_form(connection,fixture[f"{side}_team_id"],now),
                    "venue": recent_form(connection,fixture[f"{side}_team_id"],now,side)} for side in ("home","away")}
    research = research_for_fixture(connection, fixture, now)
    # Show explicitly linked supplemental results where global identity/history
    # is incomplete, with a league-only label and the original source per game.
    if research:
        for side in ('home', 'away'):
            team = research['teams'][side]
            if forms[side]['overall']['matches'] == 0:
                forms[side] = {'overall': team['current']['summary'], 'venue': team['venue_form'],
                               'scope': team['current']['competition'] + ' · 本赛季联赛补充资料'}
    warnings = []
    if not event: warnings.append("未找到可靠的国际赔率关联")
    if market["status"]=="missing_fresh_odds": warnings.append("没有足够新鲜的赛前赔率")
    if market["status"]=="insufficient_sources": warnings.append("有效来源少于3组，仅作有限参考，不留入正式成绩单")
    if market["status"]=="market_disagreement": warnings.append("各来源概率分歧较大，暂停正式留档")
    if squads["injury_status"]=="not_available": warnings.append("缺少24小时内伤停记录；不能视为没有伤停")
    if squads["lineup_status"]!="confirmed": warnings.append("双方首发尚未完整确认")
    if any(f["overall"]["matches"]<5 for f in forms.values()): warnings.append("近期战绩样本不足5场，或球队历史身份尚未打通")
    return {"match_id": fixture["match_id"], "competition": fixture["competition"],
            "competition_code": fixture["competition_code"], "home_team": localize_team(fixture["home_team"]),
            "away_team": localize_team(fixture["away_team"]),
            "home_team_original": fixture["home_team"], "away_team_original": fixture["away_team"],
            "home_team_id": fixture["home_team_id"], "away_team_id": fixture["away_team_id"],
            "kickoff_utc": fixture["kickoff_utc"].isoformat(), "as_of_utc": now.isoformat(),
            "version": VERSION,"probability_kind": "market_estimate", "market": market,
            "event_id": event.provider_event_id if event else None, "forms": forms,
            "squads": squads,"warnings": warnings,"ready_to_archive": market["status"]=="ready",
            "research": research}


def list_cards(days=30):
    now = datetime.now(UTC)
    with connect() as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        items = [build_card(connection,f,now) for f in fixtures(connection,now,days)]
    return {"as_of_utc": now.isoformat(),"items": items,"method": VERSION,"window_days": days,
            "pipeline": load_runtime_state(PROJECT_ROOT/'output/runtime/reference.state.json')}


def archive_card(connection, fixture, now, phase):
    # Use a fresh local clock here, not an API-supplied historical timestamp.
    if fixture["kickoff_utc"] <= now:
        raise ValueError("Cannot archive a match that has already started")
    card = build_card(connection,fixture,now,phase)
    encoded = json.dumps(card,ensure_ascii=False,sort_keys=True,default=str)
    status = "saved" if card["ready_to_archive"] else card["market"]["status"]
    saved = None
    if card["ready_to_archive"]:
        saved_at = datetime.now(UTC)
        if saved_at >= fixture["kickoff_utc"] or (phase != "manual" and due_checkpoint(fixture["kickoff_utc"],saved_at) != phase):
            return {"match_id":fixture["match_id"],"phase":phase,"status":"window_elapsed","reference_id":None}
        p = card["market"]["probabilities"]
        saved = connection.execute(
            """INSERT INTO analytics.reference_snapshots(match_id,kickoff_utc,phase,version,
              created_at_utc,probability_home,probability_draw,probability_away,payload,content_sha256)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT(match_id,kickoff_utc,phase,version) DO NOTHING RETURNING reference_id""",
            (fixture["match_id"],fixture["kickoff_utc"],phase,VERSION,saved_at,*p,Jsonb(json.loads(encoded)),hashlib.sha256(encoded.encode()).hexdigest()),
        ).fetchone()
        if saved is None: status="already_saved"
    connection.execute(
        "INSERT INTO audit.reference_attempts(match_id,kickoff_utc,phase,status,details) VALUES(%s,%s,%s,%s,%s)",
        (fixture["match_id"],fixture["kickoff_utc"],phase,status,Jsonb({"warnings":card["warnings"],"source_count":card["market"]["source_count"]})),
    )
    return {"match_id":fixture["match_id"],"phase":phase,"status":status,"reference_id":saved["reference_id"] if saved else None}


def settle(connection, now):
    rows=connection.execute(
        """SELECT r.reference_id,r.kickoff_utc AS archived_kickoff,m.*,
             EXISTS(SELECT 1 FROM core.source_entity_mappings sem WHERE sem.entity_type='match'
               AND sem.canonical_id=m.match_id AND sem.source_name IN ('football-data.org','api-football')) AS primary_result
           FROM analytics.reference_snapshots r JOIN core.matches m USING(match_id)
           WHERE r.kickoff_utc<%s ORDER BY r.reference_id""",(now,)).fetchall()
    count=0
    for r in rows:
        outcome=None
        hg,ag=r["regulation_home_goals"],r["regulation_away_goals"]
        if r["kickoff_utc"] != r["archived_kickoff"] or r["status"] in {"cancelled","postponed"}:
            outcome="void"
        elif r["status"]=="finished" and r["primary_result"] and hg is not None and ag is not None:
            outcome="H" if hg>ag else "D" if hg==ag else "A"
        if outcome:
            result=connection.execute(
                """INSERT INTO analytics.reference_results(reference_id,outcome,home_goals,away_goals,source,evidence)
                 VALUES(%s,%s,%s,%s,'core_primary_regulation_result',%s)
                 ON CONFLICT(reference_id) DO UPDATE SET outcome=EXCLUDED.outcome,home_goals=EXCLUDED.home_goals,
                    away_goals=EXCLUDED.away_goals,evidence=EXCLUDED.evidence,settled_at_utc=now()
                 WHERE (reference_results.outcome,reference_results.home_goals,reference_results.away_goals)
                    IS DISTINCT FROM (EXCLUDED.outcome,EXCLUDED.home_goals,EXCLUDED.away_goals)
                 RETURNING reference_id""",
                (r["reference_id"],outcome,hg,ag,Jsonb({"match_id":r["match_id"],"duration":r["result_duration"],"source_updated_at":str(r["updated_at_utc"])})),
            ).fetchone()
            count+=bool(result)
    return count


def run_cycle():
    with connect() as connection:
        # One owner across API/manual runs and the independent Windows task.
        if not connection.execute("SELECT pg_try_advisory_xact_lock(hashtext('zucai-reference')) AS locked").fetchone()["locked"]:
            return {"status":"busy","saved":0,"settled":0,"attempts":[]}
        now=datetime.now(UTC)
        attempts=[]
        for fixture in fixtures(connection,now,2):
            phase=due_checkpoint(fixture["kickoff_utc"],now)
            if phase:
                exists=connection.execute("SELECT 1 FROM analytics.reference_snapshots WHERE match_id=%s AND kickoff_utc=%s AND phase=%s AND version=%s",
                    (fixture["match_id"],fixture["kickoff_utc"],phase,VERSION)).fetchone()
                if not exists: attempts.append(archive_card(connection,fixture,now,phase))
        settled=settle(connection,now)
    return {"status":"ok","saved":sum(a["status"]=="saved" for a in attempts),"settled":settled,"attempts":attempts,"at_utc":now.isoformat()}


def save_manual(match_id):
    with connect() as connection:
        now=datetime.now(UTC)
        rows=fixtures(connection,now,30,match_id)
        if not rows: raise LookupError("Match is unavailable, started, or outside the next 30 days")
        return archive_card(connection,rows[0],now,"manual")


def scorecard(phase="h1", competition=None):
    with connect() as connection:
        rows=connection.execute(
            """SELECT r.reference_id,r.match_id,r.phase,r.created_at_utc,r.kickoff_utc,
               r.payload->>'competition' AS competition,r.payload->>'home_team' AS home_team,
               r.payload->>'away_team' AS away_team,
               ARRAY[r.probability_home,r.probability_draw,r.probability_away] AS probabilities,
               s.outcome,s.home_goals,s.away_goals
             FROM analytics.reference_snapshots r LEFT JOIN analytics.reference_results s USING(reference_id)
             WHERE r.phase=%s AND r.version=%s AND (%s::text IS NULL OR r.payload->>'competition_code'=%s)
             ORDER BY r.created_at_utc DESC""",(phase,VERSION,competition,competition)).fetchall()
        attempts=connection.execute("SELECT status,count(*) AS count FROM audit.reference_attempts WHERE attempted_at_utc>now()-interval '7 days' GROUP BY status").fetchall()
    groups=sorted(set(r["competition"] for r in rows))
    return {"phase":phase,"version":VERSION,"summary":summarize(rows),"recent":rows[:100],"attempts":attempts,
            "leagues":[{"competition":name,**summarize([r for r in rows if r["competition"]==name])} for name in groups],
            "policy":"One match per phase/version; manual samples separate; no retroactive predictions or profit claims."}
