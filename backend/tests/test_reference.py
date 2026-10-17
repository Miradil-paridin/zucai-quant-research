from datetime import UTC, datetime, timedelta
import math

import pytest

from app.reference.model import due_checkpoint, market_reference, probabilities, score_probability, summarize
from app.jingcai.consensus_gate import evaluate_gate
from app.data_sources import normalize_football_data
from app.jingcai.team_names import consensus_names

NOW=datetime(2026,9,21,8,tzinfo=UTC)


def quote(book, hours=0, odds=(2,3.5,4), snapshot_id=1):
    return dict(bookmaker=book,observed_at_utc=NOW-timedelta(hours=hours),snapshot_id=snapshot_id,
                odd_home=odds[0],odd_draw=odds[1],odd_away=odds[2])


def test_probability_calculation_and_invalid_prices():
    assert probabilities([2,3.5,4]) == pytest.approx([14/29,8/29,7/29])
    for value in (None,0,1,float('nan'),float('inf')):
        assert probabilities([value,2,3]) is None


def test_only_distinct_families_and_no_future_or_stale_quotes():
    result=market_reference([quote('unibet_fr'),quote('unibet_se'),quote('pinnacle'),
        quote('betfair_ex_eu'),quote('williamhill',hours=4),quote('coolbet',hours=-1)],NOW,NOW+timedelta(hours=6))
    assert result['source_count']==2
    assert result['status']=='insufficient_sources'
    assert sum(result['probabilities'])==pytest.approx(1)


def test_three_fresh_sources_and_disagreement_gate():
    q=[quote('pinnacle'),quote('williamhill'),quote('coolbet')]
    assert market_reference(q,NOW,NOW+timedelta(hours=6))['status']=='ready'
    q.append(quote('betclic_fr',odds=(10,9,1.2)))
    assert market_reference(q,NOW,NOW+timedelta(hours=6))['status']=='market_disagreement'
    assert market_reference(q,NOW,NOW)['status']=='missing_fresh_odds'


def test_checkpoint_window_is_not_retroactively_filled():
    assert due_checkpoint(NOW+timedelta(hours=24),NOW)=='h24'
    assert due_checkpoint(NOW+timedelta(hours=5.75),NOW)=='h6'
    assert due_checkpoint(NOW+timedelta(minutes=40),NOW)=='h1'
    assert due_checkpoint(NOW+timedelta(minutes=20),NOW) is None
    assert due_checkpoint(NOW-timedelta(minutes=1),NOW) is None


def test_scorecard_separates_pending_and_void_from_accuracy():
    r=summarize([{'probabilities':[.6,.3,.1],'outcome':'H'},
                 {'probabilities':[.6,.3,.1],'outcome':'A'},
                 {'probabilities':[.6,.3,.1],'outcome':None},
                 {'probabilities':[.6,.3,.1],'outcome':'void'}])
    assert (r['saved'],r['settled'],r['pending'],r['void'],r['accuracy'])==(4,2,1,1,.5)
    assert sum(b['count'] for b in r['calibration'])==6
    assert r['accuracy_interval_95'][0]<.5<r['accuracy_interval_95'][1]
    assert score_probability([.6,.3,.1],'H')['log_loss']==pytest.approx(-math.log(.6))
    assert summarize([])['accuracy'] is None


def test_reference_checkpoint_cannot_bypass_budget_or_failure_backoff():
    args=dict(now_utc=NOW,last_success=NOW,latest_started=NOW,latest_finished=NOW,
        latest_status='succeeded',monthly_requests=100,quota_remaining=300,match_rows=[],reference_keys=('soccer_epl',))
    assert evaluate_gate(**args).reason=='reference_checkpoint'
    assert evaluate_gate(**{**args,'monthly_requests':400}).reason=='monthly_budget_exhausted'
    assert evaluate_gate(**{**args,'latest_status':'failed'}).reason.startswith('failure_backoff')


def test_extra_time_final_score_is_not_treated_as_regulation_result():
    f={'id':1,'homeTeam':{'id':1,'name':'A'},'awayTeam':{'id':2,'name':'B'},
       'utcDate':'2026-09-20T19:00:00Z','score':{'duration':'EXTRA_TIME','fullTime':{'home':3,'away':2}}}
    assert normalize_football_data(f)['regulation_score']=={}
    f['score']['regularTime']={'home':1,'away':1}
    assert normalize_football_data(f)['regulation_score']=={'home':1,'away':1}


def test_reference_identity_accepts_audited_provider_spellings_only():
    assert consensus_names("Leeds United FC") == {"Leeds United"}
    assert consensus_names("布伦特福德") == {"Brentford"}
    assert consensus_names("Real Racing Club de Santander") == {"Real Racing Club de Santander"}
    assert consensus_names("Leeds City") == set()
