"""Opt-in integration check; all fixture/result changes are rolled back."""
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from app.database import connect
from app.reference import service


@pytest.mark.skipif(os.getenv('RUN_REFERENCE_DB_TESTS') != '1', reason='requires local reference database')
def test_immutable_archive_regulation_settlement_and_correction(monkeypatch):
    monkeypatch.setattr(service, 'VERSION', 'integration-test-' + str(uuid4()))
    monkeypatch.setattr(service, 'build_card', lambda *args: {
        'ready_to_archive': True, 'warnings': [],
        'market': {'probabilities': [.5, .3, .2], 'source_count': 3, 'status': 'ready'},
        'test_only': True,
    })
    with connect() as connection:
        try:
            now = datetime.now(UTC)
            fixture = next(f for f in service.fixtures(connection, now) if connection.execute(
                "SELECT 1 FROM core.source_entity_mappings WHERE entity_type='match' AND canonical_id=%s AND source_name IN ('football-data.org','api-football')",
                (f['match_id'],)).fetchone())
            saved = service.archive_card(connection, fixture, now, 'manual')
            assert saved['status'] == 'saved'
            reference_id = saved['reference_id']
            original = connection.execute('SELECT * FROM analytics.reference_snapshots WHERE reference_id=%s', (reference_id,)).fetchone()
            assert service.archive_card(connection, fixture, now, 'manual')['status'] == 'already_saved'
            for operation in ('UPDATE analytics.reference_snapshots SET probability_home=.6', 'DELETE FROM analytics.reference_snapshots'):
                with pytest.raises(psycopg.errors.RaiseException, match='immutable'):
                    with connection.transaction():
                        connection.execute(operation + ' WHERE reference_id=%s', (reference_id,))

            connection.execute("UPDATE core.matches SET status='finished',home_goals=3,away_goals=2,regulation_home_goals=NULL,regulation_away_goals=NULL,result_duration='EXTRA_TIME' WHERE match_id=%s", (fixture['match_id'],))
            after = fixture['kickoff_utc'] + timedelta(hours=5)
            service.settle(connection, after)
            assert connection.execute('SELECT * FROM analytics.reference_results WHERE reference_id=%s', (reference_id,)).fetchone() is None
            connection.execute('UPDATE core.matches SET regulation_home_goals=1,regulation_away_goals=1 WHERE match_id=%s', (fixture['match_id'],))
            service.settle(connection, after)
            result = connection.execute('SELECT * FROM analytics.reference_results WHERE reference_id=%s', (reference_id,)).fetchone()
            assert (result['outcome'], result['home_goals'], result['away_goals']) == ('D', 1, 1)
            service.settle(connection, after)
            assert connection.execute('SELECT * FROM analytics.reference_results WHERE reference_id=%s', (reference_id,)).fetchone() == result
            connection.execute("UPDATE core.matches SET status='postponed' WHERE match_id=%s", (fixture['match_id'],))
            service.settle(connection, after)
            assert connection.execute('SELECT outcome FROM analytics.reference_results WHERE reference_id=%s', (reference_id,)).fetchone()['outcome'] == 'void'
            assert connection.execute('SELECT * FROM analytics.reference_snapshots WHERE reference_id=%s', (reference_id,)).fetchone() == original
        finally:
            connection.rollback()
