"""Validate and atomically import the reviewed historical dossier batch."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import connect
from app.reference.context import DEFAULT_DATASET, import_dataset, validate_dataset
from app.reference.service import install_schema


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path, default=DEFAULT_DATASET)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    data = validate_dataset(json.loads(args.file.read_text(encoding='utf-8')))
    if args.validate_only:
        print(json.dumps({'status': 'valid', 'matches': len(data['items'])}))
        return
    with connect() as connection:
        install_schema(connection)
        result = import_dataset(connection, data)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
