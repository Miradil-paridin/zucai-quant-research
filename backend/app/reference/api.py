from fastapi import APIRouter, HTTPException, Query
from .service import list_cards, run_cycle, save_manual, scorecard
from ..database import connect
from .context import list_dossiers

router = APIRouter(prefix="/api/reference", tags=["reference"])


@router.get("/dossiers")
def historical_context():
    return list_dossiers()


@router.get("")
def references(days: int = Query(default=30, ge=1, le=30)):
    return list_cards(days)


@router.get("/scorecard")
def performance(phase: str = Query(default="h1", pattern="^(h24|h6|h1|manual)$"), competition: str | None = None):
    return scorecard(phase, competition)


@router.post("/run")
def run():
    return run_cycle()


@router.post("/{match_id}/snapshot")
def snapshot(match_id: int):
    try:
        return save_manual(match_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/archives/{reference_id}")
def archive(reference_id: int):
    with connect() as connection:
        row = connection.execute("SELECT * FROM analytics.reference_snapshots WHERE reference_id=%s", (reference_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Reference archive not found")
    return row


@router.get("/research/{research_id}")
def research_archive(research_id: int):
    with connect() as connection:
        row = connection.execute('SELECT * FROM analytics.prematch_research WHERE research_id=%s', (research_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail='Research archive not found')
    return row
