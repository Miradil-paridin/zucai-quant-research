"""Verify all official PDFs against the reviewed dataset. Does not alter research."""
import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import PROJECT_ROOT
from app.reference.context import DEFAULT_DATASET, validate_dataset
from app.reference.uefa_lineups import parse_lineup_pdf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, default=DEFAULT_DATASET)
    parser.add_argument('--cache', type=Path, default=PROJECT_ROOT / 'output/research/ucl-context')
    parser.add_argument('--download', action='store_true', help='Fetch missing PDFs over HTTPS; existing files are verified, not overwritten')
    args = parser.parse_args()
    data = validate_dataset(json.loads(args.file.read_text(encoding='utf-8')))
    sources = {s['id']: s for s in data['sources']}
    args.cache.mkdir(parents=True, exist_ok=True)
    results = []
    for item in data['items']:
        source = sources[item['lineup']['source_id']]
        target = args.cache / f"{item['uefa_id']}.pdf"
        if not target.exists():
            if not args.download:
                raise FileNotFoundError(f'{target}; use --download to retrieve missing PDFs')
            from curl_cffi import requests
            response = requests.get(source['url'], impersonate='chrome', timeout=30)
            response.raise_for_status()
            if not response.content.startswith(b'%PDF'):
                raise ValueError(f"Expected PDF: {item['uefa_id']}")
            content = response.content
        else:
            content = target.read_bytes()
        if hashlib.sha256(content).hexdigest() != source['sha256']:
            raise ValueError(f"Source changed: {item['uefa_id']}; manually review and create a new research version")
        lineup = parse_lineup_pdf(content, kickoff_utc=item['kickoff_utc'], expected_header=source['expected_header'])
        if any(lineup[s] != item['lineup'][s] for s in ('home', 'away')):
            raise ValueError(f"Official lineup disagrees with dataset: {item['uefa_id']}")
        if not target.exists():
            target.write_bytes(content)
        results.append({'fixture_id': item['primary_fixture_id'], 'uefa_id': item['uefa_id'], 'starters': 22, 'sha256': source['sha256'], 'status': 'verified'})
    report = {'verified_at_utc': datetime.now(UTC).isoformat(), 'version': data['version'], 'matches': len(results), 'starters': sum(r['starters'] for r in results), 'items': results}
    (args.cache / 'verification.json').write_bytes((json.dumps(report, indent=2)+'\n').encode())
    print(json.dumps({k: v for k,v in report.items() if k != 'items'}))


if __name__ == '__main__':
    main()
