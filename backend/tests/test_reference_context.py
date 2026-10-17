import copy
import json
import os
from uuid import uuid4

import psycopg
import pytest

from app.database import connect
from app.reference.context import DEFAULT_DATASET, import_dataset, summary, validate_dataset
from app.reference.uefa_lineups import parse_lineup_text


@pytest.fixture
def data():
    return json.loads(DEFAULT_DATASET.read_text(encoding='utf-8'))


def test_reviewed_batch_is_complete_but_not_point_in_time(data):
    validate_dataset(data)
    stats = summary(data['items'], data['sources'])
    assert stats['matches'] == 18 and stats['starters'] == 396
    assert stats['verified_prematch_snapshots'] == 0
    assert stats['teams_with_unknown_availability'] == 1
    assert stats['starter_resolutions'] == 5
    assert all(not i['eligible_for_prediction_backtest'] for i in data['items'])


@pytest.mark.parametrize('damage', ['eligible', 'prematch', 'duplicate', 'identity', 'source', 'resolution', 'probability', 'coverage'])
def test_invalid_research_cannot_be_imported(data, damage):
    item = data['items'][0]
    if damage == 'eligible':
        item['eligible_for_prediction_backtest'] = True
    elif damage == 'prematch':
        data['collected_at_utc'] = '2026-09-01T00:00:00Z'
    elif damage == 'duplicate':
        item['lineup']['home'][1] = copy.deepcopy(item['lineup']['home'][0])
    elif damage == 'identity':
        item['uefa_id'] += 100
    elif damage == 'source':
        item['availability']['home']['facts'][0]['source_id'] = 'not-a-source'
    elif damage == 'resolution':
        item['availability']['home']['facts'][0].update(roster_resolution='started', roster_name='Made Up', resolution_source_id=item['lineup']['source_id'])
    elif damage == 'probability':
        item['analysis']['probability_adjustment'] = .1
    else:
        item['availability']['home']['coverage'] = 'complete'
    with pytest.raises(ValueError):
        validate_dataset(data)


def test_actual_starter_does_not_erase_original_uncertainty(data):
    facts = [f for i in data['items'] for side in ('home', 'away') for f in i['availability'][side]['facts']]
    resolved = [f for f in facts if f['roster_resolution'] == 'started']
    assert len(resolved) == 5
    assert all(f['reported_status'] == 'doubtful' for f in resolved)
    assert any(f['player'] == 'Julián Alvarez' for f in resolved)


def test_pdf_parser_rejects_swapped_teams_short_rosters_and_duplicate_numbers():
    rows = [f'  {n} Home Player {n}      {n} Away Player {n}' for n in range(1, 12)]
    header = 'Tactical Line-ups\nMatchday 1 - Tuesday 08 September 2026\nVenue\nHome FC      Away FC\n'
    args = dict(kickoff_utc='2026-09-08T19:00:00Z', expected_header='Home FC Away FC')
    valid = header + '\n'.join(rows) + '\n\nPitch diagram\n  99 Substitute      99 Substitute'
    assert len(parse_lineup_text(valid, **args)['home']) == 11
    for bad in (valid.replace('Home FC      Away FC', 'Away FC      Home FC'),
                header + '\n'.join(rows[:-1]), valid.replace(rows[-1], rows[0]), valid.replace('08 September', '09 September')):
        with pytest.raises(ValueError):
            parse_lineup_text(bad, **args)


@pytest.mark.skipif(os.getenv('RUN_REFERENCE_DB_TESTS') != '1', reason='requires local database')
def test_import_is_atomic_idempotent_and_separate_from_predictions(data):
    data['version'] = 'test-' + str(uuid4())
    with connect() as connection:
        try:
            before = connection.execute('SELECT reference_id,content_sha256 FROM analytics.reference_snapshots ORDER BY reference_id').fetchall()
            assert import_dataset(connection, data)['status'] == 'imported'
            assert import_dataset(connection, data)['status'] == 'already_imported'
            rows = connection.execute('SELECT count(*) AS n FROM analytics.match_context_dossiers WHERE version=%s', (data['version'],)).fetchone()
            assert rows['n'] == 18
            changed = copy.deepcopy(data)
            changed['items'][0]['analysis']['text'] += ' changed'
            with pytest.raises(ValueError, match='immutable'):
                import_dataset(connection, changed)
            with pytest.raises(psycopg.errors.RaiseException, match='immutable'):
                with connection.transaction():
                    connection.execute('DELETE FROM analytics.context_batches WHERE version=%s', (data['version'],))
            changed['version'] += '-mismatch'
            changed['items'][-1]['home_source_team_id'] = changed['items'][-1]['away_source_team_id']
            with pytest.raises(ValueError, match='Team identity mismatch'):
                import_dataset(connection, changed)
            assert not connection.execute('SELECT 1 FROM analytics.context_batches WHERE version=%s', (changed['version'],)).fetchone()
            after = connection.execute('SELECT reference_id,content_sha256 FROM analytics.reference_snapshots ORDER BY reference_id').fetchall()
            assert before == after
        finally:
            connection.rollback()
