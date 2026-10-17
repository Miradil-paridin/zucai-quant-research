"""Reviewed, source-linked qualitative research captured before kickoff.

This ledger is independent of market probabilities and their scoring sample.
Season records are scoped to explicitly reviewed team identities, never fuzzy
merged into the global team table. Aggregates are calculated from the evidence.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlparse

from psycopg.types.json import Jsonb


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Research timestamps must include timezone')
    return parsed


def form(records, venue=None):
    rows = sorted((r for r in records if not venue or r['venue'] == venue),
                  key=lambda r: r['date'], reverse=True)
    return {'matches': len(rows),
            'wins': sum(r['goals_for'] > r['goals_against'] for r in rows),
            'draws': sum(r['goals_for'] == r['goals_against'] for r in rows),
            'losses': sum(r['goals_for'] < r['goals_against'] for r in rows),
            'goals_for': sum(r['goals_for'] for r in rows),
            'goals_against': sum(r['goals_against'] for r in rows),
            'latest_date': rows[0]['date'] if rows else None,
            'records': [{**r, 'result': 'W' if r['goals_for'] > r['goals_against'] else
                        'D' if r['goals_for'] == r['goals_against'] else 'L'} for r in rows]}


def validate_report(report, now):
    cutoff = timestamp(report['as_of_utc'])
    kickoff = timestamp(report['kickoff_utc'])
    if not cutoff <= now < kickoff:
        raise ValueError('Research must be captured and imported before kickoff')
    if report['kind'] != 'qualitative_assessment' or report['probabilities'] is not None:
        raise ValueError('Qualitative research must not claim calibrated probabilities')
    if report['result_scope'] != 'regulation_90_minutes':
        raise ValueError('Research covers the 90-minute result only')
    sources = {s['id']: s for s in report['sources']}
    if not sources or len(sources) != len(report['sources']):
        raise ValueError('Unique sources required')
    for source in sources.values():
        if urlparse(source['url']).scheme != 'https' or not urlparse(source['url']).netloc:
            raise ValueError('HTTPS source URL required')
        if timestamp(source['retrieved_at_utc']) > cutoff:
            raise ValueError('Source captured after research cutoff')
        if source.get('published_on') and date.fromisoformat(source['published_on']) > cutoff.date():
            raise ValueError('Source published after research cutoff')
    def references(ids):
        if not ids or not set(ids) <= sources.keys():
            raise ValueError('Unknown or missing evidence source')
    for side in ('home', 'away'):
        team = report['teams'][side]
        if team['team_id'] != report[f'{side}_team_id'] or not team['source_team_name']:
            raise ValueError('Team identity mismatch')
        for season in ('previous', 'current'):
            sample = team[season]
            seen = set()
            for row in sample['records']:
                key = (row['date'], row['opponent'], row['venue'])
                if key in seen:
                    raise ValueError('Duplicate match evidence')
                seen.add(key)
                # A day-only result needs a complete prior day; no guess about
                # whether a same-day match finished before the research cutoff.
                if date.fromisoformat(row['date']) >= cutoff.date():
                    raise ValueError('Match result is not before research cutoff')
                if row['venue'] not in ('home', 'away') or any(
                    type(row[k]) is not int or row[k] < 0 for k in ('goals_for', 'goals_against')):
                    raise ValueError('Invalid result')
                references([row['source_id']])
    for section in ('support', 'counterpoints', 'news'):
        for claim in report[section]:
            references(claim['source_ids'])
    if not report['support'] or not report['counterpoints'] or not report['review_triggers']:
        raise ValueError('A lean needs evidence, counterevidence and review conditions')
    return report


def import_reports(connection, reports):
    saved = []
    for report in reports:
        now = datetime.now(UTC)
        validate_report(report, now)
        fixture = connection.execute(
            'SELECT * FROM core.matches WHERE match_id=%s FOR SHARE', (report['match_id'],)).fetchone()
        if (not fixture or fixture['status'] != 'scheduled'
                or fixture['kickoff_utc'] != timestamp(report['kickoff_utc'])
                or any(fixture[f'{s}_team_id'] != report[f'{s}_team_id'] for s in ('home', 'away'))):
            raise ValueError('Report identity/kickoff does not match the scheduled fixture')
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        existing = connection.execute(
            'SELECT research_id,content_sha256 FROM analytics.prematch_research '
            'WHERE match_id=%s AND kickoff_utc=%s AND version=%s',
            (report['match_id'], fixture['kickoff_utc'], report['version'])).fetchone()
        if existing:
            if existing['content_sha256'] != digest:
                raise ValueError('Research version is immutable; create a new version')
            saved.append({'research_id': existing['research_id'], 'status': 'already_saved'})
            continue
        row = connection.execute(
            'INSERT INTO analytics.prematch_research '
            '(match_id,kickoff_utc,version,as_of_utc,created_at_utc,payload,content_sha256) '
            'VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING research_id',
            (report['match_id'], fixture['kickoff_utc'], report['version'], timestamp(report['as_of_utc']),
             datetime.now(UTC), Jsonb(report), digest)).fetchone()
        saved.append({'research_id': row['research_id'], 'status': 'saved'})
    return saved


def research_for_fixture(connection, fixture, now):
    row = connection.execute(
        'SELECT * FROM analytics.prematch_research WHERE match_id=%s AND kickoff_utc=%s '
        'AND as_of_utc<=%s AND created_at_utc<=%s '
        "AND (payload->>'home_team_id')::bigint=%s AND (payload->>'away_team_id')::bigint=%s "
        'ORDER BY created_at_utc DESC,research_id DESC LIMIT 1',
        (fixture['match_id'], fixture['kickoff_utc'], now, now,
         fixture['home_team_id'], fixture['away_team_id'])).fetchone()
    if not row:
        return None
    report = row['payload']
    # Reading a report never makes its evidence fresh. After 24h or inside the
    # final 90 minutes, explicitly ask for news/lineup review of the saved lean.
    report['review_due'] = (now - row['as_of_utc'] >= timedelta(hours=24)
                            or fixture['kickoff_utc'] - now <= timedelta(minutes=90))
    report['research_id'] = row['research_id']
    report['saved_at_utc'] = row['created_at_utc'].isoformat()
    for side, team in report['teams'].items():
        for season in ('previous', 'current'):
            team[season]['summary'] = form(team[season]['records'])
        team['venue_form'] = form(team['current']['records'], side)
    return report
