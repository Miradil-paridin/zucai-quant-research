"""Download public UEFA post-match PDFs and event feeds for the reviewed 18 fixtures."""
import argparse
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from curl_cffi import requests
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import PROJECT_ROOT
from app.reference.context import DEFAULT_DATASET, validate_dataset


def collect(uid, root):
    for kind in ('TS','FR'):
        target = root/f'{uid}_{kind}.pdf'
        metadata = target.with_suffix('.json')
        if not target.exists() or not metadata.exists():
            url = f'https://www.uefa.com/newsfiles/UCL/2027/{uid}_{kind}.pdf'
            response = requests.get(url, impersonate='chrome', timeout=30)
            response.raise_for_status()
            if not response.content.startswith(b'%PDF'):
                raise ValueError('Source did not return a PDF')
            if target.exists() and target.read_bytes() != response.content:
                raise ValueError('Existing source changed; use a new cache directory for the new version')
            target.write_bytes(response.content)
            metadata.write_bytes(json.dumps(dict(url=url, sha256=hashlib.sha256(response.content).hexdigest(),
                                                  retrieved_at_utc=datetime.now(UTC).isoformat())).encode())
        if not target.with_suffix('.txt').exists():
            text = '\n'.join(p.extract_text(extraction_mode='layout') for p in PdfReader(target).pages)
            target.with_suffix('.txt').write_bytes(text.encode())
    paths = {kind: root/f'{uid}_{kind}.json' for kind in ('match','events','api_meta')}
    if not all(p.exists() for p in paths.values()):
        url = f'https://match.uefa.com/v5/matches/{uid}'
        match = requests.get(url, impersonate='chrome', timeout=30)
        match.raise_for_status()
        events, pages = [], []
        for offset in range(0,5000,500):
            page_url = url+f'/events?filter=ALL&offset={offset}&limit=500'
            response = requests.get(page_url, impersonate='chrome', timeout=30)
            response.raise_for_status()
            page = response.json()
            if not isinstance(page,list):
                raise ValueError('Expected event list')
            events.extend(page)
            pages.append(dict(url=page_url,sha256=hashlib.sha256(response.content).hexdigest()))
            if len(page)<500:
                break
        else:
            raise ValueError('Event pagination limit reached')
        content = {'match': match.content, 'events': json.dumps(events,ensure_ascii=False).encode(),
                   'api_meta': json.dumps(dict(match_url=url,match_sha256=hashlib.sha256(match.content).hexdigest(),
                                               event_pages=pages,retrieved_at_utc=datetime.now(UTC).isoformat())).encode()}
        for kind in ('match','events'):
            if paths[kind].exists() and json.loads(paths[kind].read_bytes()) != json.loads(content[kind]):
                raise ValueError('Existing API source changed; use a new cache directory')
        for kind,p in paths.items():
            p.write_bytes(content[kind])
    return {'uefa_id':uid,'status':'cached','events':len(json.loads(paths['events'].read_bytes()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=PROJECT_ROOT/'output/research/ucl-postmatch')
    args = parser.parse_args()
    args.cache.mkdir(parents=True,exist_ok=True)
    data = validate_dataset(json.loads(DEFAULT_DATASET.read_bytes()))
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(lambda item: collect(item['uefa_id'],args.cache),data['items']):
            print(json.dumps(result),flush=True)


if __name__=='__main__':
    main()
