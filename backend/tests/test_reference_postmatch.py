import copy
import json
from pathlib import Path

import pytest

from app.reference.context import DEFAULT_DATASET, validate_dataset, summary
from app.reference.postmatch import normalize_events, parse_statistics


@pytest.fixture
def data():
    return json.loads(DEFAULT_DATASET.read_bytes())


def test_official_result_stats_goals_and_cards_reconcile(data):
    validate_dataset(data)
    counts=summary(data['items'],data['sources'])
    assert counts['matches_with_statistics']==18
    assert (counts['goals'],counts['substitutions'],counts['card_events'])==(69,168,74)
    assert all(i['postmatch']['expected_goals'] is None for i in data['items'])


@pytest.mark.parametrize('change',['half_total','shots','score','own_goal','chronology','coach','duplicate','xg','source'])
def test_wrong_aggregation_timeline_or_provenance_rejected(data,change):
    item = next(i for i in data['items'] if i['uefa_id']==2049562)
    p=item['postmatch']
    if change=='half_total':p['statistics']['shots']['first_half']['home']+=1
    elif change=='shots':
        p['statistics']['shots']['first_half']['home']+=1
        p['statistics']['shots']['total']['home']+=1
    elif change=='score':next(e for e in p['events'] if e['type']=='GOAL')['score_after']['home']+=1
    elif change=='own_goal':next(e for e in p['events'] if e['subtype']=='OWN')['scoring_side']='home'
    elif change=='chronology':p['events'].reverse()
    elif change=='coach':
        p=next(i for i in data['items'] if i['uefa_id']==2049569)['postmatch']
        next(e for e in p['events'] if e['type']=='RED_CARD' and e['actor']['role']=='COACH')['on_pitch']=True
    elif change=='duplicate':p['events'].append(copy.deepcopy(p['events'][0]))
    elif change=='xg':p['expected_goals']={'home':2,'away':1}
    else:p['source_ids'].append(item['lineup']['source_id'])
    with pytest.raises(ValueError):validate_dataset(data)


def test_running_score_own_goal_and_coach_red_card_are_distinct(data):
    sporting=next(i for i in data['items'] if i['uefa_id']==2049562)
    goals=[e for e in sporting['postmatch']['events'] if e['type']=='GOAL']
    assert goals[0]['subtype']=='OWN' and goals[0]['side']=='home' and goals[0]['scoring_side']=='away'
    assert goals[0]['score_after']=={'home':0,'away':1}
    assert goals[-1]['score_after']=={'home':3,'away':1}
    slavia=next(i for i in data['items'] if i['uefa_id']==2049569)
    cards=[e for e in slavia['postmatch']['events'] if e['type']=='RED_CARD']
    assert len(cards)==2 and sum(e['on_pitch'] for e in cards)==1


def test_offline_raw_parser_and_substitution_state(data):
    root=Path(__file__).resolve().parents[2]/'output/research/ucl-postmatch'
    if not (root/'2049553_TS.txt').exists():pytest.skip('Optional local source archive')
    item=next(i for i in data['items'] if i['uefa_id']==2049553)
    sources={s['id']:s for s in data['sources']}
    header=sources[item['lineup']['source_id']]['expected_header']
    raw=(root/'2049553_TS.txt').read_text(encoding='utf-8')
    assert parse_statistics(raw,item,header)==item['postmatch']['statistics']
    with pytest.raises(ValueError,match='date mismatch'):
        parse_statistics(raw.replace('08 September','09 September'),item,header)
    match=json.loads((root/'2049553_match.json').read_bytes())
    events=json.loads((root/'2049553_events.json').read_bytes())
    normalized=normalize_events(events,match,item)
    assert normalized==item['postmatch']['events']
    substitute=next(e for e in events if e['type']=='SUBSTITUTION')
    substitute['primaryActor']['person']['internationalName']='Not on the pitch'
    with pytest.raises(ValueError,match='not on pitch'):normalize_events(events,match,item)
