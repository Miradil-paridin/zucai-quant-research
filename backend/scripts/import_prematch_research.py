"""Import reviewed qualitative research without altering market forecasts."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import connect
from app.reference.prematch import import_reports
from app.reference.service import install_schema


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', type=Path)
    args = parser.parse_args()
    reports = json.loads(args.file.read_text(encoding='utf-8'))['items']
    with connect() as connection:
        install_schema(connection)
        result = import_reports(connection, reports)
    print(json.dumps(result))
