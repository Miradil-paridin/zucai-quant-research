"""Refresh this season's six primary competitions for reference/form coverage.

One request per competition, seven seconds apart. Raw provider responses are
retained; this does not backfill predictions or historical odds timestamps.
"""
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.data_sources import normalize_football_data, supported_football_data
from app.repository import repository
from app.collector_runtime import append_jsonl


def main():
    from curl_cffi import requests
    today=datetime.now(UTC).date()
    start=today.replace(month=7,day=1) if today.month>=7 else today.replace(year=today.year-1,month=7,day=1)
    result=[]
    for index,code in enumerate(('PL','PD','BL1','SA','FL1','CL')):
        if index: time.sleep(7)
        batch=repository.start_batch('football-data.org','reference_season_refresh')
        try:
            url=f'https://api.football-data.org/v4/competitions/{code}/matches'
            response=requests.get(url,params={'dateFrom':start.isoformat(),'dateTo':(today+timedelta(days=30)).isoformat()},
                headers={'X-Auth-Token':os.environ['FOOTBALL_DATA_API_KEY']},timeout=25,impersonate='chrome')
            response.raise_for_status()
            payload=response.json()
            fixtures=[normalize_football_data(m) for m in payload.get('matches',[]) if supported_football_data(m)]
            if not fixtures: raise ValueError('No supported fixtures; empty result is not complete coverage')
            resource=repository.persist_fixture_response(batch_id=batch,source='football-data.org',
                payload={'source_url':url,'response':payload},fixtures=fixtures,source_entity_id=f'{code}:{start}:{today}')
            repository.finish_batch(batch,request_count=1,record_count=len(fixtures))
            result.append({'competition':code,'status':'ok',**resource})
        except Exception as error:
            repository.fail_batch(batch,error,request_count=1)
            result.append({'competition':code,'status':'error','error':f'{type(error).__name__}: {error}'})
        if sys.stdout is not None:
            print(json.dumps(result[-1]),flush=True)
    append_jsonl(ROOT.parent/'output/runtime/reference-season-refresh.log',{'at_utc':datetime.now(UTC).isoformat(),'resources':result})
    return int(any(r['status']!='ok' for r in result))


if __name__=='__main__':
    raise SystemExit(main())
