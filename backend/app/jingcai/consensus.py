"""国际市场共识赔率采集器 (The Odds API)。

只查询竞彩当前在售联赛对应的 sport, 控制免费额度(500 请求/月)。
每个 bookmaker 的 h2h 赔率以带 captured_at_utc 的快照入库,
内容不变不重复写入。与 jc_matches 的关联(中英文队名匹配)留给
分析阶段处理, 采集阶段保持原始数据。
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from psycopg.types.json import Jsonb

from ..database import connect

PROVIDER = "the-odds-api"
BASE_URL = "https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"

# 竞彩联赛名 -> The Odds API sport key(覆盖竞彩常开售联赛)
SPORT_KEY_BY_LEAGUE = {
    "美国职业大联盟": "soccer_usa_mls",
    "挪威超级联赛": "soccer_norway_eliteserien",
    "韩国职业联赛": "soccer_korea_kleague1",
    "日本职业联赛": "soccer_japan_j_league",
    "英格兰超级联赛": "soccer_epl",
    "西班牙甲级联赛": "soccer_spain_la_liga",
    "德国甲级联赛": "soccer_germany_bundesliga",
    "意大利甲级联赛": "soccer_italy_serie_a",
    "法国甲级联赛": "soccer_france_ligue_one",
    "瑞典超级联赛": "soccer_sweden_allsvenskan",
    "芬兰超级联赛": "soccer_finland_veikkausliiga",
    "丹麦超级联赛": "soccer_denmark_superliga",
    "荷兰甲级联赛": "soccer_netherlands_eredivisie",
    "葡萄牙超级联赛": "soccer_portugal_primeira_liga",
    "巴西甲级联赛": "soccer_brazil_campeonato",
    "英格兰冠军联赛": "soccer_efl_champ",
    "欧洲冠军联赛": "soccer_uefa_champs_league",
    "欧罗巴联赛": "soccer_uefa_europa_league",
    "澳大利亚超级联赛": "soccer_australia_aleague",
    "墨西哥超级联赛": "soccer_mexico_ligamx",
    "阿根廷甲级联赛": "soccer_argentina_primera_division",
}


def _api_key() -> str:
    value = os.getenv("ODDS_API_KEY")
    if not value:
        raise RuntimeError("ODDS_API_KEY is not configured in backend/.env")
    return value


def active_sport_keys() -> list[str]:
    """竞彩当前在售(近两天可见)联赛对应的 sport key 列表。"""

    with connect() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT league_name FROM jingcai.jc_matches
            WHERE last_seen_at_utc > now() - interval '2 days'
            """
        ).fetchall()
    keys: list[str] = []
    unmapped: list[str] = []
    for row in rows:
        sport_key = SPORT_KEY_BY_LEAGUE.get(row["league_name"] or "")
        if sport_key and sport_key not in keys:
            keys.append(sport_key)
        elif not sport_key:
            unmapped.append(row["league_name"])
    if unmapped:
        keys.append("__unmapped__:" + ",".join(sorted(set(unmapped))))
    return keys


def fetch_sport_odds(sport_key: str, timeout_seconds: float = 25.0) -> tuple[list[dict], str | None]:
    from curl_cffi import requests

    response = requests.get(
        BASE_URL.format(sport_key=sport_key),
        params={
            "apiKey": _api_key(),
            "regions": "eu",
            "markets": "h2h",
            "oddsFormat": "decimal",
        },
        timeout=timeout_seconds,
    )
    if response.status_code != 200:
        raise RuntimeError(f"the-odds-api {sport_key} HTTP {response.status_code}: {response.text[:200]}")
    remaining = response.headers.get("x-requests-remaining")
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"the-odds-api {sport_key} 返回非数组 JSON")
    return payload, remaining


def _event_rows(event: dict[str, Any], sport_key: str) -> list[dict[str, Any]]:
    home = event.get("home_team")
    away = event.get("away_team")
    rows: list[dict[str, Any]] = []
    for bookmaker in event.get("bookmakers") or []:
        for market in bookmaker.get("markets") or []:
            if market.get("key") != "h2h":
                continue
            odds = {"home": None, "draw": None, "away": None}
            for outcome in market.get("outcomes") or []:
                name, price = outcome.get("name"), outcome.get("price")
                if price is None:
                    continue
                if name == home:
                    odds["home"] = str(price)
                elif name == away:
                    odds["away"] = str(price)
                elif name == "Draw":
                    odds["draw"] = str(price)
            if None in odds.values():
                continue
            content_hash = hashlib.sha256(
                "|".join(
                    [str(event.get("id")), str(bookmaker.get("key")),
                     odds["home"], odds["draw"], odds["away"]]
                ).encode("utf-8")
            ).hexdigest()
            rows.append(
                {
                    "sport_key": sport_key,
                    "bookmaker": str(bookmaker.get("key")),
                    "provider_event_id": str(event.get("id")),
                    "home_team": home,
                    "away_team": away,
                    "commence_time_utc": event.get("commence_time"),
                    "odd_home": odds["home"],
                    "odd_draw": odds["draw"],
                    "odd_away": odds["away"],
                    "content_hash": content_hash,
                    "raw": {"last_update": market.get("last_update")},
                }
            )
    return rows


def _insert_if_changed(connection: Any, row: dict[str, Any]) -> bool:
    latest = connection.execute(
        """
        SELECT content_hash FROM jingcai.consensus_odds_snapshots
        WHERE provider = %s AND provider_event_id = %s AND bookmaker = %s
        ORDER BY captured_at_utc DESC, consensus_snapshot_id DESC
        LIMIT 1
        """,
        (PROVIDER, row["provider_event_id"], row["bookmaker"]),
    ).fetchone()
    if latest and latest["content_hash"] == row["content_hash"]:
        return False
    connection.execute(
        """
        INSERT INTO jingcai.consensus_odds_snapshots (
            provider, sport_key, bookmaker, provider_event_id, home_team, away_team,
            commence_time_utc, odd_home, odd_draw, odd_away,
            content_hash, raw_payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            PROVIDER,
            row["sport_key"],
            row["bookmaker"],
            row["provider_event_id"],
            row["home_team"],
            row["away_team"],
            row["commence_time_utc"],
            row["odd_home"],
            row["odd_draw"],
            row["odd_away"],
            row["content_hash"],
            Jsonb(row["raw"]),
        ),
    )
    return True


def collect_consensus_once() -> dict[str, Any]:
    sport_keys = active_sport_keys()
    summary: dict[str, Any] = {
        "sports_queried": 0,
        "events_seen": 0,
        "snapshots_inserted": 0,
        "snapshots_unchanged": 0,
        "quota_remaining": None,
        "unmapped_leagues": None,
        "errors": [],
    }
    real_keys = [key for key in sport_keys if not key.startswith("__unmapped__:")]
    for key in sport_keys:
        if key.startswith("__unmapped__:"):
            summary["unmapped_leagues"] = key.split(":", 1)[1]
    if not real_keys:
        summary["warning"] = "竞彩在售联赛均未映射到 sport key, 未发起请求"
        return summary
    with connect() as connection:
        for sport_key in real_keys:
            try:
                events, remaining = fetch_sport_odds(sport_key)
            except Exception as error:
                summary["errors"].append(f"{sport_key}: {error}")
                continue
            summary["sports_queried"] += 1
            if remaining is not None:
                summary["quota_remaining"] = remaining
            summary["events_seen"] += len(events)
            for event in events:
                for row in _event_rows(event, sport_key):
                    if _insert_if_changed(connection, row):
                        summary["snapshots_inserted"] += 1
                    else:
                        summary["snapshots_unchanged"] += 1
        connection.commit()
    return summary
