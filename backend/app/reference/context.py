"""Source-linked historical research. Deliberately isolated from prediction inputs."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from urllib.parse import urlparse

from psycopg.types.json import Jsonb

from ..database import PROJECT_ROOT, connect

DEFAULT_DATASET = PROJECT_ROOT / 'data/research/ucl-2026-27-md1-context.json'
STATUSES = {'reported_out', 'suspended', 'doubtful', 'not_registered', 'returning', 'no_absences_reported'}
TIMINGS = {'reported_pre_match', 'mixed_updates', 'post_match', 'official_match_sheet'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def instant(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError('Timestamp must include timezone')
    return parsed


def validate_dataset(data):
    """Fail closed on temporal leakage, incomplete rosters and dangling provenance."""
    if not data.get('dataset_id') or not data.get('version') or not data.get('items'):
        raise ValueError('Dataset identity, version and items are required')
    captured = instant(data['collected_at_utc'])
    sources = {s['id']: s for s in data['sources']}
    if len(sources) != len(data['sources']):
        raise ValueError('Duplicate source ID')
    for source in sources.values():
        url = urlparse(source['url'])
        if url.scheme != 'https' or not url.hostname or url.username or url.password:
            raise ValueError('Source must have a public HTTPS URL')
        if source.get('point_in_time_verified') is not False or source['timing'] not in TIMINGS:
            raise ValueError('Historical sources cannot assert a verified pre-match snapshot')
        instant(source['reviewed_at_utc'])
    seen = set()
    seen_uefa = set()
    for item in data['items']:
        if item['primary_fixture_id'] in seen or item['uefa_id'] in seen_uefa:
            raise ValueError('Duplicate fixture identity')
        seen.add(item['primary_fixture_id'])
        seen_uefa.add(item['uefa_id'])
        if (item.get('provenance') != 'historical_backfill' or item.get('point_in_time_verified') is not False
                or item.get('eligible_for_prediction_backtest') is not False or captured <= instant(item['kickoff_utc'])):
            raise ValueError('Historical dossier cannot enter prediction backtests')
        refs = set(item['source_ids'])
        if not refs <= sources.keys():
            raise ValueError('Unknown source reference')
        lineup = item['lineup']
        if lineup['status'] != 'official' or lineup['source_id'] not in refs:
            raise ValueError('Official lineup source required')
        official = sources[lineup['source_id']]
        if (official['url'] != f"https://www.uefa.com/newsfiles/UCL/2027/{item['uefa_id']}_LU.pdf"
                or official['authority'] != 'official' or not re.fullmatch('[0-9a-f]{64}', official.get('sha256', ''))):
            raise ValueError('UEFA identity and PDF hash required')
        if instant(official['retrieved_at_utc']) <= instant(item['kickoff_utc']):
            raise ValueError('This batch is exclusively post-match collection')
        for side in ('home', 'away'):
            players = lineup[side]
            names = [p['name'].strip() for p in players]
            numbers = [p['number'] for p in players]
            if (len(players) != 11 or len(set(names)) != 11 or len(set(numbers)) != 11
                    or not all(names) or not all(isinstance(n, int) and 0 < n < 1000 for n in numbers)):
                raise ValueError('Each team requires 11 distinct named starters and shirt numbers')
            availability = item['availability'][side]
            if availability['coverage'] not in {'partial', 'unknown'}:
                raise ValueError('Do not claim complete medical coverage')
            if bool(availability['facts']) != (availability['coverage'] == 'partial'):
                raise ValueError('Availability coverage contradicts facts')
            for fact in availability['facts']:
                if fact['reported_status'] not in STATUSES or fact['source_id'] not in refs:
                    raise ValueError('Availability fact needs a supported status and source')
                if fact['roster_resolution'] == 'started':
                    if fact.get('roster_name') not in names or fact.get('resolution_source_id') != lineup['source_id']:
                        raise ValueError('Starter resolution must be supported by the official roster')
                elif fact['roster_resolution'] != 'not_verified':
                    raise ValueError('Unknown roster resolution')
        analysis = item['analysis']
        if (analysis['kind'] != 'retrospective_qualitative_assessment' or analysis['probability_adjustment'] is not None
                or not set(analysis['source_ids']) <= refs):
            raise ValueError('Research cannot invent numerical probability adjustments')
        review = item.get('post_match_review')
        if review and (review['kind'] != 'post_match_observation' or not set(review['source_ids']) <= refs
                       or any(sources[k]['timing'] != 'post_match' for k in review['source_ids'])):
            raise ValueError('Post-match observations need explicitly post-match sources')
        if item.get('postmatch'):
            from .postmatch import validate_postmatch
            validate_postmatch(item['postmatch'], item, sources)
    return data


def import_dataset(connection, data):
    validate_dataset(data)
    content_hash = digest(data)
    previous = connection.execute(
        'SELECT content_sha256 FROM analytics.context_batches WHERE dataset_id=%s AND version=%s',
        (data['dataset_id'], data['version']),
    ).fetchone()
    if previous:
        if previous['content_sha256'] != content_hash:
            raise ValueError('Published research version is immutable; create a new version')
        return {'status': 'already_imported', 'matches': len(data['items']), 'sha256': content_hash}
    resolved = []
    for item in data['items']:
        matches = connection.execute(
            """SELECT m.* FROM core.source_entity_mappings sem
               JOIN core.matches m ON m.match_id=sem.canonical_id
               WHERE sem.source_name='football-data.org' AND sem.entity_type='match' AND sem.source_entity_id=%s""",
            (str(item['primary_fixture_id']),),
        ).fetchall()
        if len(matches) != 1:
            raise ValueError(f"Unresolved fixture {item['primary_fixture_id']}")
        match = matches[0]
        if (match['kickoff_utc'] != instant(item['kickoff_utc']) or match['status'] != 'finished'
                or (match['home_goals'], match['away_goals']) != (item['score']['home'], item['score']['away'])):
            raise ValueError(f"Fixture date/result mismatch: {item['primary_fixture_id']}")
        for side in ('home', 'away'):
            mapped = connection.execute(
                """SELECT canonical_id FROM core.source_entity_mappings
                   WHERE source_name='football-data.org' AND entity_type='team' AND source_entity_id=%s""",
                (str(item[f'{side}_source_team_id']),),
            ).fetchall()
            if len(mapped) != 1 or mapped[0]['canonical_id'] != match[f'{side}_team_id']:
                raise ValueError(f"Team identity mismatch: {item['primary_fixture_id']} {side}")
        resolved.append((match['match_id'], item))
    connection.execute(
        'INSERT INTO analytics.context_batches(dataset_id,version,content_sha256,payload) VALUES(%s,%s,%s,%s)',
        (data['dataset_id'], data['version'], content_hash, Jsonb(data)),
    )
    for match_id, item in resolved:
        connection.execute(
            'INSERT INTO analytics.match_context_dossiers(dataset_id,version,match_id,payload) VALUES(%s,%s,%s,%s)',
            (data['dataset_id'], data['version'], match_id, Jsonb(item)),
        )
    return {'status': 'imported', 'matches': len(resolved), 'sha256': content_hash}


def summary(items, sources):
    facts = [f for i in items for side in ('home', 'away') for f in i['availability'][side]['facts']]
    return dict(matches=len(items), starters=sum(len(i['lineup'][s]) for i in items for s in ('home', 'away')),
                sources=len(sources), availability_reports=len(facts),
                teams_with_reports=sum(i['availability'][s]['coverage'] == 'partial' for i in items for s in ('home', 'away')),
                teams_with_unknown_availability=sum(i['availability'][s]['coverage'] == 'unknown' for i in items for s in ('home', 'away')),
                starter_resolutions=sum(f['roster_resolution'] == 'started' for f in facts),
                post_match_reviews=sum(bool(i.get('post_match_review')) for i in items),
                verified_prematch_snapshots=0,
                matches_with_statistics=sum(bool(i.get('postmatch')) for i in items),
                goals=sum(e['type']=='GOAL' for i in items for e in i.get('postmatch',{}).get('events',[])),
                substitutions=sum(e['type']=='SUBSTITUTION' for i in items for e in i.get('postmatch',{}).get('events',[])),
                card_events=sum(e['type'] in ('YELLOW_CARD','RED_CARD') for i in items for e in i.get('postmatch',{}).get('events',[])))


def list_dossiers():
    with connect() as connection:
        batch = connection.execute('SELECT * FROM analytics.context_batches ORDER BY imported_at_utc DESC,dataset_id,version LIMIT 1').fetchone()
        if not batch:
            return {'items': [], 'sources': [], 'summary': summary([], [])}
        rows = connection.execute(
            'SELECT match_id,payload FROM analytics.match_context_dossiers WHERE dataset_id=%s AND version=%s ORDER BY match_id',
            (batch['dataset_id'], batch['version']),
        ).fetchall()
    data = batch['payload']
    items = sorted([{**r['payload'], 'match_id': r['match_id']} for r in rows], key=lambda i: (i['kickoff_utc'], i['primary_fixture_id']))
    return {**data, 'items': items, 'summary': summary(items, data['sources']),
            'imported_at_utc': batch['imported_at_utc'], 'content_sha256': batch['content_sha256']}
