import copy
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.database import connect
from app.reference import service
from app.reference.prematch import form, import_reports, research_for_fixture, timestamp, validate_report


DATA = json.loads((Path(__file__).resolve().parents[2] / 'data/research/efl-trophy-2026-09-22-prematch.json').read_text(encoding='utf-8'))['items']


def test_reviewed_results_are_complete_and_recent_round_is_included():
    expected = {10908: (0, 6, 1, 8, 10), 10909: (1, 1, 5, 6, 13),
                10910: (1, 1, 5, 5, 12), 10911: (2, 2, 3, 13, 13),
                10912: (1, 4, 2, 7, 9), 10913: (3, 4, 0, 11, 6)}
    for report in DATA:
        validate_report(report, timestamp(report['as_of_utc']) + timedelta(minutes=1))
        for team in report['teams'].values():
            assert len(team['previous']['records']) == 46
            summary = form(team['current']['records'])
            assert summary['matches'] == 7
            assert tuple(summary[k] for k in ('wins', 'draws', 'losses', 'goals_for', 'goals_against')) == expected[team['team_id']]
            assert summary['latest_date'] == ('2026-09-17' if team['team_id'] == 10908 else '2026-09-19')
    assert form(DATA[2]['teams']['away']['current']['records'], 'away')['draws'] == 3


@pytest.mark.parametrize('issue', ['future_capture', 'future_publication', 'future_result', 'duplicate', 'source', 'team', 'probability', 'started'])
def test_invalid_or_postmatch_evidence_is_rejected(issue):
    report = copy.deepcopy(DATA[0])
    now = timestamp(report['as_of_utc']) + timedelta(minutes=1)
    row = report['teams']['home']['current']['records'][0]
    if issue == 'future_capture':
        report['sources'][0]['retrieved_at_utc'] = (now + timedelta(days=2)).isoformat()
    elif issue == 'future_publication':
        report['sources'][0]['published_on'] = (now + timedelta(days=2)).date().isoformat()
    elif issue == 'future_result':
        row['date'] = now.date().isoformat()
    elif issue == 'duplicate':
        report['teams']['home']['current']['records'].append(copy.deepcopy(row))
    elif issue == 'source':
        row['source_id'] = 'unknown'
    elif issue == 'team':
        report['home_team_id'] = 123
    elif issue == 'probability':
        report['probabilities'] = [.5, .3, .2]
    else:
        now = timestamp(report['kickoff_utc'])
    with pytest.raises(ValueError):
        validate_report(report, now)


@pytest.mark.skipif(os.getenv('RUN_REFERENCE_DB_TESTS') != '1', reason='requires local reference database')
def test_research_ledger_identity_immutability_and_freshness():
    with connect() as connection:
        try:
            service.install_schema(connection)
            fixture = service.fixtures(connection, datetime.now(UTC))[0]
            report = copy.deepcopy(DATA[0])
            report.update(match_id=fixture['match_id'], kickoff_utc=fixture['kickoff_utc'].isoformat(),
                          version='test-' + str(uuid4()), as_of_utc=datetime.now(UTC).isoformat())
            for side in ('home', 'away'):
                report[f'{side}_team_id'] = fixture[f'{side}_team_id']
                report['teams'][side]['team_id'] = fixture[f'{side}_team_id']
            before = connection.execute('SELECT count(*) AS n FROM analytics.reference_snapshots').fetchone()['n']
            saved = import_reports(connection, [report])[0]
            assert saved['status'] == 'saved'
            assert import_reports(connection, [report])[0]['status'] == 'already_saved'
            found = research_for_fixture(connection, fixture, datetime.now(UTC))
            assert found['research_id'] == saved['research_id']
            assert found['probabilities'] is None
            later = fixture['kickoff_utc'] - timedelta(minutes=60)
            assert research_for_fixture(connection, fixture, later)['review_due']
            moved = {**fixture, 'kickoff_utc': fixture['kickoff_utc'] + timedelta(days=1)}
            assert research_for_fixture(connection, moved, datetime.now(UTC)) is None
            swapped = {**fixture, 'home_team_id': fixture['away_team_id']}
            assert research_for_fixture(connection, swapped, datetime.now(UTC)) is None
            before_capture = timestamp(report['as_of_utc']) - timedelta(minutes=1)
            assert research_for_fixture(connection, fixture, before_capture) is None
            report['lean'] = 'Changed after saving'
            with pytest.raises(ValueError, match='immutable'):
                import_reports(connection, [report])
            for sql in ('UPDATE analytics.prematch_research SET version=\'changed\'', 'DELETE FROM analytics.prematch_research'):
                with pytest.raises(psycopg.errors.RaiseException, match='immutable'):
                    with connection.transaction():
                        connection.execute(sql + ' WHERE research_id=%s', (saved['research_id'],))
            report['home_team_id'] = fixture['away_team_id']
            report['teams']['home']['team_id'] = fixture['away_team_id']
            with pytest.raises(ValueError, match='identity'):
                import_reports(connection, [report])
            assert connection.execute('SELECT count(*) AS n FROM analytics.reference_snapshots').fetchone()['n'] == before
        finally:
            connection.rollback()
