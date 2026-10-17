"""UEFA post-match facts, kept separate from historical pre-match assumptions."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

METRICS = {
    'goals': 'Goal Scored', 'shots': 'Total attempts', 'shots_on_target': 'On target',
    'shots_off_target': 'Off target', 'shots_blocked': 'Blocked', 'woodwork': 'Woodwork',
    'saves': 'Saves', 'corners': 'Corners', 'offsides': 'Offsides',
    'possession': 'Ball Possession', 'yellow_cards': 'Yellow cards', 'red_cards': 'Red cards',
    'fouls': 'Fouls committed', 'passes_attempted': 'Passes attempted',
    'passes_completed': 'Passes completed', 'pass_completion': 'Pass completion rate',
}
EVENT_TYPES = {'GOAL', 'SUBSTITUTION', 'YELLOW_CARD', 'RED_CARD'}


def normalize_name(value):
    return ''.join(c.lower() for c in unicodedata.normalize('NFKD', value) if c.isalnum())


def parse_statistics(text, item, expected_header):
    lines = text.splitlines()
    date = datetime.fromisoformat(item['kickoff_utc'].replace('Z', '+00:00'))
    months = 'January February March April May June July August September October November December'.split()
    if not lines or lines[0].strip() != 'Team Statistics Full Time':
        raise ValueError('Expected official full-time statistics sheet')
    if not re.search(rf'\b0?{date.day} {months[date.month-1]} {date.year}\b', '\n'.join(lines[:3])):
        raise ValueError('Statistics sheet date mismatch')
    title = re.fullmatch(r'\s*(.+?)\s+(\d+)\s*-\s*(\d+)\s+(.+?)\s*', lines[3])
    if not title or normalize_name(title[1]+title[4]) != normalize_name(expected_header):
        raise ValueError('Statistics sheet team order mismatch')
    if [int(title[2]), int(title[3])] != [item['score']['home'], item['score']['away']]:
        raise ValueError('Statistics sheet final score mismatch')
    stats = {}
    for key, label in METRICS.items():
        rows = [line.strip()[len(label):] for line in lines if re.match(rf'^\s*{re.escape(label)}\s+', line)]
        if len(rows) != 1:
            raise ValueError(f'Missing or ambiguous statistics row: {key}')
        values = re.findall(r'^\s*((?:\d+%?\s+){5}\d+%?)', rows[0])
        if not values:
            raise ValueError(f'Invalid statistics row: {key}')
        nums = [int(v.rstrip('%')) for v in values[0].split()]
        stats[key] = {phase: {'home': nums[n], 'away': nums[n+1]} for phase,n in [('first_half',0),('second_half',2),('total',4)]}
    validate_statistics(stats, item['score'])
    return stats


def validate_statistics(stats, score):
    if set(stats) != set(METRICS):
        raise ValueError('Incomplete statistics coverage')
    for key, phases in stats.items():
        for phase in ('first_half', 'second_half', 'total'):
            for side in ('home', 'away'):
                value = phases[phase][side]
                if type(value) is not int or value < 0 or (key in ('possession','pass_completion') and value > 100):
                    raise ValueError('Invalid statistics value')
        if key not in ('possession','pass_completion'):
            for side in ('home','away'):
                if phases['first_half'][side]+phases['second_half'][side] != phases['total'][side]:
                    raise ValueError(f'Half/full statistics disagree: {key}')
    if stats['goals']['total'] != score:
        raise ValueError('Goals disagree with final score')
    for phase in ('first_half','second_half','total'):
        if sum(stats['possession'][phase].values()) != 100:
            raise ValueError('Possession must total 100')
        for side in ('home','away'):
            if stats['shots'][phase][side] != sum(stats[k][phase][side] for k in ('shots_on_target','shots_off_target','shots_blocked')):
                raise ValueError('Shot categories disagree')
            if stats['passes_completed'][phase][side] > stats['passes_attempted'][phase][side]:
                raise ValueError('Completed passes exceed attempts')


def event_order(event):
    t = event.get('time', {})
    phase = {'FIRST_HALF': 1, 'SECOND_HALF': 2}.get(event.get('phase'), 9)
    return phase, t.get('minute',0), t.get('injuryMinute',0), t.get('second',0), event.get('timestamp',''), event['id']


def player(actor):
    person = actor.get('person', {})
    return {'id': str(person.get('id') or person.get('person',{}).get('id') or ''),
            'name': person.get('internationalName') or ('教练（接口未给姓名）' if actor.get('type') == 'COACH' else '姓名未提供'),
            'role': actor.get('type','UNKNOWN')}


def normalize_events(raw, match, item):
    if (str(match['id']) != str(item['uefa_id']) or match['status'] != 'FINISHED'
            or match['kickOffTime']['dateTime'] != item['kickoff_utc'] or match['score']['total'] != item['score']):
        raise ValueError('Official event match identity/date/result mismatch')
    team_ids = {str(match[f'{s}Team']['id']): s for s in ('home','away')}
    if len(team_ids) != 2 or not any(e['type'] == 'FULL_TIME' for e in raw):
        raise ValueError('Incomplete event feed')
    if len({e['id'] for e in raw}) != len(raw) or any(str(e['matchId']) != str(item['uefa_id']) for e in raw):
        raise ValueError('Duplicate or foreign event')
    # A sent-off coach/substitute does not reduce the on-pitch player count.
    active = {s: {normalize_name(p['name']) for p in item['lineup'][s]} for s in ('home','away')}
    result = []
    score = {'home':0,'away':0}
    for e in sorted(raw, key=event_order):
        if e['type'] not in EVENT_TYPES:
            continue
        actor = e.get('primaryActor',{})
        side = team_ids.get(str(actor.get('team',{}).get('id')))
        if side is None:
            raise ValueError('Event team unresolved')
        time = e['time']
        if e.get('phase') not in ('FIRST_HALF','SECOND_HALF') or not 0 <= time['minute'] <= 90:
            raise ValueError('Unexpected period/time')
        person = player(actor)
        minute = str(time['minute']) + (f"+{time['injuryMinute']}" if time.get('injuryMinute') else '')
        n = normalize_name(person['name'])
        event = {'id': e['id'], 'type': e['type'], 'side': side, 'time': time, 'minute_label': minute+'′',
                 'phase': e['phase'], 'actor': person, 'subtype': e.get('subType'), 'detail': e.get('detail')}
        if e['type'] == 'GOAL':
            beneficiary = ('away' if side == 'home' else 'home') if e.get('subType') == 'OWN' else side
            score[beneficiary] += 1
            event.update(scoring_side=beneficiary, score_after=dict(score))
        elif e['type'] == 'SUBSTITUTION':
            incoming = player(e['secondaryActor'])
            if n not in active[side]:
                raise ValueError(f"Substituted player is not on pitch: {item['uefa_id']} {person['name']}")
            active[side].remove(n)
            if normalize_name(incoming['name']) in active[side]:
                raise ValueError('Incoming player is already active')
            active[side].add(normalize_name(incoming['name']))
            event.update(incoming=incoming, outgoing=person)
        elif e['type'] == 'RED_CARD':
            on_pitch = person['role'] == 'PLAYER' and n in active[side]
            event['on_pitch'] = on_pitch
            if on_pitch:
                active[side].remove(n)
        result.append(event)
    if score != item['score']:
        raise ValueError('Goal event totals disagree with final result')
    return result


def validate_postmatch(post, item, sources):
    if post['status'] != 'verified_official' or post['expected_goals'] is not None:
        raise ValueError('Unknown post-match status or unverified xG')
    validate_statistics(post['statistics'], item['score'])
    refs = set(post['source_ids'])
    if not refs <= set(item['source_ids']) or any(sources[k]['timing'] != 'post_match' for k in refs):
        raise ValueError('Post-match provenance mismatch')
    events = post['events']
    if len({e['id'] for e in events}) != len(events):
        raise ValueError('Duplicate normalized event')
    if [event_order(e)[:4] for e in events] != sorted(event_order(e)[:4] for e in events):
        raise ValueError('Event chronology incorrect')
    score = {'home':0, 'away':0}
    half_score = dict(score)
    for e in events:
        if e['type'] not in EVENT_TYPES or e['side'] not in ('home','away'):
            raise ValueError('Unknown normalized event type/team')
        if e['type']=='RED_CARD' and e['actor']['role']!='PLAYER' and e['on_pitch']:
            raise ValueError('Coach red card cannot reduce on-pitch player count')
        if e['type'] == 'GOAL':
            beneficiary = ('away' if e['side']=='home' else 'home') if e['subtype']=='OWN' else e['side']
            if beneficiary != e['scoring_side']:
                raise ValueError('Own goal assigned to wrong team')
            score[beneficiary] += 1
            if e['score_after'] != score:
                raise ValueError('Running goal score incorrect')
            if e['phase']=='FIRST_HALF':
                half_score[beneficiary] += 1
    if score != item['score'] or half_score != post['statistics']['goals']['first_half']:
        raise ValueError('Event goals disagree with half/full statistics')
    for kind, key in [('YELLOW_CARD','yellow_cards'),('RED_CARD','red_cards')]:
        for side in ('home','away'):
            if sum(e['type']==kind and e['side']==side for e in events) != post['statistics'][key]['total'][side]:
                raise ValueError('Event cards disagree with official statistics')
