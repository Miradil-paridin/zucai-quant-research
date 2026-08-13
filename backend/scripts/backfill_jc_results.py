"""回填竞彩历史赛果与最终固定赔率(胜平负)。

用法: python scripts/backfill_jc_results.py 2024-07-01 2026-06-30
限速 + 断点续传(state 文件记录已完成窗口), 可随时中断重跑。
"""

from __future__ import annotations

import json
import random
import sys
import time
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from psycopg.types.json import Jsonb  # noqa: E402

from app.database import connect, initialize_database  # noqa: E402

URL = (
    "https://webapi.sporttery.cn/gateway/uniform/football/"
    "getUniformMatchResultV1.qry"
)
STATE_PATH = BACKEND_ROOT.parent / "output" / "runtime" / "jc_backfill_state.json"
WINDOW_DAYS = 2
PAGE_SIZE = 30
SLEEP_BASE = 0.8


def _fetch(begin: date, end: date, page_no: int) -> dict:
    from curl_cffi import requests

    response = requests.get(
        URL,
        params={
            "matchBeginDate": begin.isoformat(),
            "matchEndDate": end.isoformat(),
            "leagueId": "",
            "pageSize": str(PAGE_SIZE),
            "pageNo": str(page_no),
            "isFix": "0",
            "matchPage": "1",
            "pcOrWap": "1",
        },
        impersonate="chrome",
        headers={"Referer": "https://www.sporttery.cn/"},
        timeout=25,
    )
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code} for {begin}~{end} p{page_no}")
    return response.json().get("value") or {}


def _decimal(value) -> str | None:
    if value in (None, ""):
        return None
    try:
        float(value)
    except (TypeError, ValueError):
        return None
    return str(value)


def _upsert(connection, rec: dict) -> None:
    connection.execute(
        """
        INSERT INTO jingcai.jc_result_history (
            official_match_id, match_date, match_num_str, league_id,
            league_abbr, league_name, home_team, away_team,
            home_team_full, away_team_full, goal_line,
            odd_home, odd_draw, odd_away, win_flag,
            ht_score, ft_score, pool_status, raw_payload
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (official_match_id) DO UPDATE
        SET odd_home = EXCLUDED.odd_home,
            odd_draw = EXCLUDED.odd_draw,
            odd_away = EXCLUDED.odd_away,
            win_flag = EXCLUDED.win_flag,
            ft_score = EXCLUDED.ft_score,
            pool_status = EXCLUDED.pool_status,
            raw_payload = EXCLUDED.raw_payload,
            fetched_at_utc = now()
        """,
        (
            int(rec["matchId"]),
            rec.get("matchDate"),
            rec.get("matchNumStr"),
            rec.get("leagueId"),
            rec.get("leagueNameAbbr"),
            rec.get("leagueName"),
            rec.get("homeTeam") or "?",
            rec.get("awayTeam") or "?",
            rec.get("allHomeTeam"),
            rec.get("allAwayTeam"),
            rec.get("goalLine"),
            _decimal(rec.get("h")),
            _decimal(rec.get("d")),
            _decimal(rec.get("a")),
            rec.get("winFlag"),
            rec.get("sectionsNo1"),
            rec.get("sectionsNo999"),
            rec.get("poolStatus"),
            Jsonb(rec),
        ),
    )


def _load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"done_windows": []}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    start = date.fromisoformat(sys.argv[1])
    end = date.fromisoformat(sys.argv[2])
    initialize_database()
    state = _load_state()
    done = set(state["done_windows"])
    total_rows = 0
    windows = []
    cursor = start
    while cursor <= end:
        windows.append(cursor)
        cursor += timedelta(days=WINDOW_DAYS)
    with connect() as connection:
        for begin in windows:
            window_end = min(begin + timedelta(days=WINDOW_DAYS - 1), end)
            key = begin.isoformat()
            if key in done:
                continue
            page_no, pages = 1, 1
            while page_no <= pages:
                value = _fetch(begin, window_end, page_no)
                pages = int(value.get("pages") or 1)
                for rec in value.get("matchResult") or []:
                    if rec.get("matchId") is None:
                        continue
                    _upsert(connection, rec)
                    total_rows += 1
                page_no += 1
                time.sleep(SLEEP_BASE + random.random() * 0.4)
            connection.commit()
            done.add(key)
            state["done_windows"] = sorted(done)
            _save_state(state)
            print(f"{key} ok (rows so far {total_rows})", flush=True)
    print(
        json.dumps(
            {
                "at_utc": datetime.now(timezone.utc).isoformat(),
                "windows": len(windows),
                "rows_upserted": total_rows,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
