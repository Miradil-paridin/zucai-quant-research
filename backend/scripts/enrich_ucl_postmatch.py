"""Build a new dossier version from cached official post-match facts and reviewed analysis."""
import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import PROJECT_ROOT
from app.reference.context import DEFAULT_DATASET, validate_dataset
from app.reference.postmatch import normalize_events, parse_statistics, validate_postmatch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--cache', type=Path, default=PROJECT_ROOT/'output/research/ucl-postmatch')
    args = parser.parse_args()
    data = json.loads(DEFAULT_DATASET.read_text(encoding='utf-8'))
    if args.version == data['version']:
        raise ValueError('Specify a new research version')
    reviews = json.loads((PROJECT_ROOT/'data/research/ucl-md1-postmatch-reviews.json').read_text(encoding='utf-8'))
    sources = {s['id']:s for s in data['sources']}
    now = datetime.now(UTC).isoformat()
    for item in data['items']:
        uid = item['uefa_id']
        def read(suffix):
            return json.loads((args.cache/f'{uid}_{suffix}.json').read_text(encoding='utf-8'))
        match, raw, api_meta = read('match'), read('events'), read('api_meta')
        stats = parse_statistics((args.cache/f'{uid}_TS.txt').read_text(encoding='utf-8'), item,
                                 sources[item['lineup']['source_id']]['expected_header'])
        events = normalize_events(raw, match, item)
        refs = []
        for suffix, title in [('TS','UEFA正式全场统计（含上下半场）'),('FR','UEFA正式赛后报告（含比赛事件）')]:
            meta = read(suffix)
            if hashlib.sha256((args.cache/f'{uid}_{suffix}.pdf').read_bytes()).hexdigest() != meta['sha256']:
                raise ValueError('Cached PDF hash mismatch')
            key = f'uefa-{uid}-{suffix.lower()}'
            sources[key] = dict(id=key,title=title,authority='official',timing='post_match',point_in_time_verified=False,
                                published_label=item['kickoff_utc'][:10]+'（比赛日期）',reviewed_at_utc=now,**meta)
            refs.append(key)
        for suffix, title, url in [('match','UEFA完赛信息与赛果', api_meta['match_url']),
                                    ('events','UEFA比赛事件：进球、换人、罚牌', api_meta['event_pages'][0]['url'])]:
            key = f'uefa-{uid}-{suffix}'
            sources[key] = dict(id=key,title=title,url=url,authority='official',timing='post_match',point_in_time_verified=False,
                                published_label=item['kickoff_utc'][:10]+'（比赛日期；数据可能更正）',
                                reviewed_at_utc=now,retrieved_at_utc=api_meta['retrieved_at_utc'],
                                record_sha256=hashlib.sha256((args.cache/f'{uid}_{suffix}.json').read_bytes()).hexdigest())
            refs.append(key)
        item['source_ids'] = list(dict.fromkeys(item['source_ids']+refs))
        item['postmatch'] = dict(status='verified_official',statistics=stats,events=events,source_ids=refs,
                                expected_goals=None,expected_goals_status='not_in_official_source',
                                interpretation=reviews[str(uid)],
                                interpretation_kind='retrospective_evidence_based_assessment',
                                limitations=['本批官方统计未提供xG，不能用射门数量替代机会质量。',
                                             '赛后事实可以验证比赛过程，不能补造当时未留档的预测。'],
                                raw_event_count=len(raw))
        validate_postmatch(item['postmatch'], item, sources)
    data.update(version=args.version,collected_at_utc=now,sources=list(sources.values()),
                methodology='18场均已完赛。正式比分、分半场统计和关键事件来自UEFA，已交叉核对；阵容伤停报道另行保留。资料为赛后采集，不计入赛前预测成绩。')
    validate_dataset(data)
    DEFAULT_DATASET.write_bytes((json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps({'matches':len(data['items']),'events':sum(len(i['postmatch']['events']) for i in data['items']),
                      'sources':len(sources),'version':data['version']}))


if __name__ == '__main__':
    main()
