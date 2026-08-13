"""竞彩官方赔率采集器。

从中国竞彩网公开接口拉取在售足球赛事的胜平负(had)与让球胜平负(hhad)
固定赔率, 以带 captured_at_utc 的快照形式写入 jingcai schema。

防封锁说明: webapi.sporttery.cn 由腾讯云 EdgeOne 防护, 会封禁境外出口
IP 与非浏览器 TLS 指纹, 因此必须 (a) 从中国大陆网络直连访问,
(b) 使用 curl_cffi 的 Chrome TLS 指纹。567 状态码 = 被 WAF 拦截,
通常意味着出口 IP 在境外(检查代理分流)。
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Iterator

from psycopg.types.json import Jsonb

from ..database import connect

CALCULATOR_URL = (
    "https://webapi.sporttery.cn/gateway/jc/football/"
    "getMatchCalculatorV1.qry?poolCode=hhad,had&channel=c"
)
REQUEST_HEADERS = {
    "Referer": "https://www.sporttery.cn/",
    "Accept": "application/json, text/plain, */*",
}
POOL_CODES = ("had", "hhad")


class CollectorBlockedError(RuntimeError):
    """WAF 拦截(567 等), 大概率是出口 IP 在境外。"""


def fetch_calculator_payload(timeout_seconds: float = 20.0) -> dict[str, Any]:
    from curl_cffi import requests  # 延迟导入, 避免测试环境强依赖

    response = requests.get(
        CALCULATOR_URL,
        impersonate="chrome",
        headers=REQUEST_HEADERS,
        timeout=timeout_seconds,
    )
    if response.status_code == 567:
        raise CollectorBlockedError(
            "sporttery WAF 拦截(567): 出口 IP 可能在境外, 请检查代理分流"
        )
    if response.status_code != 200:
        raise RuntimeError(f"sporttery API HTTP {response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("sporttery API 返回非对象 JSON")
    return payload


def _walk_match_dicts(node: Any) -> Iterator[dict[str, Any]]:
    """递归寻找带 matchId 且含 had/hhad 赔率的比赛对象, 抗结构变化。"""

    if isinstance(node, dict):
        if node.get("matchId") is not None and (
            isinstance(node.get("had"), dict) or isinstance(node.get("hhad"), dict)
        ):
            yield node
        else:
            for value in node.values():
                yield from _walk_match_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_match_dicts(item)


def extract_matches(payload: dict[str, Any]) -> list[dict[str, Any]]:
    matches: dict[str, dict[str, Any]] = {}
    for node in _walk_match_dicts(payload):
        matches[str(node["matchId"])] = node
    return list(matches.values())


def _text(node: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = node.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _decimal_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        float(value)
    except (TypeError, ValueError):
        return None
    return str(value)


def _pool_row(match: dict[str, Any], pool_code: str) -> dict[str, Any] | None:
    pool = match.get(pool_code)
    if not isinstance(pool, dict):
        return None
    odd_home = _decimal_or_none(pool.get("h"))
    odd_draw = _decimal_or_none(pool.get("d"))
    odd_away = _decimal_or_none(pool.get("a"))
    if odd_home is None or odd_draw is None or odd_away is None:
        return None
    goal_line = _text(pool, "goalLine") if pool_code == "hhad" else None
    update_raw = " ".join(
        value for value in (_text(pool, "updateDate"), _text(pool, "updateTime")) if value
    ) or None
    content_hash = hashlib.sha256(
        f"{pool_code}|{goal_line}|{odd_home}|{odd_draw}|{odd_away}".encode("utf-8")
    ).hexdigest()
    return {
        "pool_code": pool_code,
        "goal_line": goal_line,
        "odd_home": odd_home,
        "odd_draw": odd_draw,
        "odd_away": odd_away,
        "official_update_raw": update_raw,
        "content_hash": content_hash,
    }


def _match_date(match: dict[str, Any]) -> date | None:
    raw = _text(match, "matchDate", "businessDate")
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _upsert_match(connection: Any, match: dict[str, Any]) -> int:
    row = connection.execute(
        """
        INSERT INTO jingcai.jc_matches (
            official_match_id, match_num_str, league_code, league_name,
            home_team, away_team, match_date_local, match_time_local,
            sell_end_local, raw_payload
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (official_match_id) DO UPDATE
        SET last_seen_at_utc = now(),
            match_num_str = EXCLUDED.match_num_str,
            sell_end_local = EXCLUDED.sell_end_local,
            raw_payload = EXCLUDED.raw_payload
        RETURNING jc_match_id
        """,
        (
            str(match["matchId"]),
            _text(match, "matchNumStr", "matchNum"),
            _text(match, "leagueCode", "leagueAbbName"),
            _text(match, "leagueAllName", "leagueAbbName", "leagueName"),
            _text(match, "homeTeamAllName", "homeTeamAbbName", "homeTeam") or "?",
            _text(match, "awayTeamAllName", "awayTeamAbbName", "awayTeam") or "?",
            _match_date(match),
            _text(match, "matchTime"),
            " ".join(
                value
                for value in (_text(match, "sellEndDate"), _text(match, "sellEndTime"))
                if value
            )
            or None,
            Jsonb(match),
        ),
    ).fetchone()
    return int(row["jc_match_id"])


def _insert_snapshot_if_changed(connection: Any, jc_match_id: int, row: dict[str, Any]) -> bool:
    latest = connection.execute(
        """
        SELECT content_hash FROM jingcai.jc_odds_snapshots
        WHERE jc_match_id = %s AND pool_code = %s
        ORDER BY captured_at_utc DESC, jc_snapshot_id DESC
        LIMIT 1
        """,
        (jc_match_id, row["pool_code"]),
    ).fetchone()
    if latest and latest["content_hash"] == row["content_hash"]:
        return False
    connection.execute(
        """
        INSERT INTO jingcai.jc_odds_snapshots (
            jc_match_id, pool_code, goal_line, odd_home, odd_draw, odd_away,
            official_update_raw, content_hash
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            jc_match_id,
            row["pool_code"],
            row["goal_line"],
            row["odd_home"],
            row["odd_draw"],
            row["odd_away"],
            row["official_update_raw"],
            row["content_hash"],
        ),
    )
    return True


def collect_once() -> dict[str, Any]:
    """拉取一次并入库, 返回采集摘要。"""

    payload = fetch_calculator_payload()
    matches = extract_matches(payload)
    summary = {
        "matches_seen": len(matches),
        "snapshots_inserted": 0,
        "snapshots_unchanged": 0,
        "pools_missing": 0,
    }
    if not matches:
        summary["warning"] = "接口返回 0 场比赛, 原始响应键: " + ",".join(
            sorted(payload.keys())
        )
        return summary
    with connect() as connection:
        for match in matches:
            jc_match_id = _upsert_match(connection, match)
            for pool_code in POOL_CODES:
                row = _pool_row(match, pool_code)
                if row is None:
                    summary["pools_missing"] += 1
                    continue
                if _insert_snapshot_if_changed(connection, jc_match_id, row):
                    summary["snapshots_inserted"] += 1
                else:
                    summary["snapshots_unchanged"] += 1
        connection.commit()
    return summary
