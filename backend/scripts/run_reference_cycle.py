"""Record due prospective checkpoints and settle finished regulation-time results."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.collector_runtime import append_jsonl, record_runtime_outcome, record_database_failure
from app.reference.service import run_cycle
from scripts.collect_consensus_odds import main as collect_consensus
from psycopg import Error as PsycopgError

STATE = ROOT.parent / "output/runtime/reference.state.json"
LOG = ROOT.parent / "output/runtime/reference.log"


def main():
    try:
        # Uses the existing cross-process lock, quota reserve and failure gate.
        # At due checkpoints it refreshes only the relevant leagues first.
        collection_exit = collect_consensus()
        result = run_cycle()
        result["odds_collection_exit"] = collection_exit
        if collection_exit:
            result["status"] = "partial"
        append_jsonl(LOG, result)
        record_runtime_outcome(STATE, result["status"], succeeded=result["status"]=="ok")
        if sys.stdout is not None:
            print(json.dumps(result, ensure_ascii=False))
        return 1 if collection_exit else 0
    except Exception as error:
        if isinstance(error, PsycopgError): record_database_failure(STATE, error)
        else: record_runtime_outcome(STATE,"error",error=f"{type(error).__name__}: {error}")
        append_jsonl(LOG,{"status":"error","error":f"{type(error).__name__}: {error}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
