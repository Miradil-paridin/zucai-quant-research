"""国际市场共识赔率采集器 (The Odds API)。

只查询竞彩当前在售联赛对应的 sport, 控制免费额度(500 请求/月)。
每个 bookmaker 的 h2h 赔率以带 captured_at_utc 的快照入库,
内容不变不重复写入。与 jc_matches 的关联(中英文队名匹配)留给
分析阶段处理, 采集阶段保持原始数据。
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Sequence

from psycopg import Error as PsycopgError
from psycopg.types.json import Jsonb

from ..database import connect

PROVIDER = "the-odds-api"
RESOURCE_TYPE = "consensus_odds"
BASE_URL = "https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"


class ProviderRequestError(RuntimeError):
    """Provider response failure retaining any returned quota information."""

    def __init__(
        self,
        sport_key: str,
        status_code: int,
        response_text: str,
        quota_remaining: str | None,
    ) -> None:
        self.status_code = status_code
        self.quota_remaining = quota_remaining
        super().__init__(
            f"the-odds-api {sport_key} HTTP {status_code}: {response_text[:200]}"
        )

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


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}")
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
    remaining = response.headers.get("x-requests-remaining")
    if response.status_code != 200:
        raise ProviderRequestError(
            sport_key,
            response.status_code,
            response.text,
            remaining,
        )
    try:
        payload = response.json()
    except Exception as error:
        raise ProviderRequestError(
            sport_key,
            response.status_code,
            f"invalid JSON ({type(error).__name__})",
            remaining,
        ) from error
    if not isinstance(payload, list):
        raise ProviderRequestError(
            sport_key,
            response.status_code,
            "返回非数组 JSON",
            remaining,
        )
    return payload, remaining


def validate_provider_configuration() -> None:
    _api_key()
    try:
        import curl_cffi  # noqa: F401
    except ImportError as error:
        raise RuntimeError("curl_cffi is required for The Odds API collection") from error


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


def _start_batch(trigger: str, sport_keys: Sequence[str]) -> int:
    report = {"trigger": trigger, "sports_planned": len(sport_keys), "sport_keys": list(sport_keys)}
    with connect() as connection:
        row = connection.execute(
            """
            INSERT INTO raw.ingestion_batches (source_name, resource_type, status, report)
            VALUES (%s, %s, 'running', %s)
            RETURNING batch_id
            """,
            (PROVIDER, RESOURCE_TYPE, Jsonb(report)),
        ).fetchone()
        connection.commit()
    return int(row["batch_id"])


def _reserve_request(batch_id: int, monthly_budget: int) -> str:
    """Count a request before sending it so crashes cannot under-report quota use."""

    with connect() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("zucai-odds-api-budget",))
        row = connection.execute(
            """
            SELECT
              (
                SELECT COALESCE(sum(request_count), 0)::integer
                FROM raw.ingestion_batches
                WHERE source_name = %s AND resource_type = %s
                  AND started_at_utc >= date_trunc('month', now())
                  AND started_at_utc < date_trunc('month', now()) + interval '1 month'
              ) AS used,
              (
                SELECT started_at_utc >= date_trunc('month', now())
                  AND started_at_utc < date_trunc('month', now()) + interval '1 month'
                FROM raw.ingestion_batches
                WHERE batch_id = %s
              ) AS batch_in_current_month
            """,
            (PROVIDER, RESOURCE_TYPE, batch_id),
        ).fetchone()
        if not row or not row["batch_in_current_month"]:
            return "batch_period_elapsed"
        if int(row["used"] or 0) >= monthly_budget:
            return "monthly_budget_exhausted"
        connection.execute(
            """
            UPDATE raw.ingestion_batches
            SET request_count = request_count + 1
            WHERE batch_id = %s
            """,
            (batch_id,),
        )
        connection.commit()
    return "reserved"


def _latest_quota_observation() -> tuple[int | None, bool | None]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT
              CASE
                WHEN report->>'quota_remaining' ~ '^\\d{1,9}$'
                  THEN (report->>'quota_remaining')::integer
                ELSE NULL
              END AS quota_remaining,
              CASE
                WHEN report->>'quota_verified' = 'true' THEN true
                WHEN report->>'quota_verified' = 'false' THEN false
                ELSE NULL
              END AS quota_verified
            FROM raw.ingestion_batches
            WHERE source_name = %s AND resource_type = %s
              AND report ? 'quota_remaining'
              AND started_at_utc >= date_trunc('month', now())
              AND started_at_utc < date_trunc('month', now()) + interval '1 month'
            ORDER BY started_at_utc DESC, batch_id DESC LIMIT 1
            """,
            (PROVIDER, RESOURCE_TYPE),
        ).fetchone()
    if not row:
        return None, None
    remaining = int(row["quota_remaining"]) if row["quota_remaining"] is not None else None
    verified = row["quota_verified"]
    # Runs created before quota_verified was introduced have a usable numeric header.
    if verified is None and remaining is not None:
        verified = True
    return remaining, verified


def _store_sport_events(sport_key: str, events: list[dict[str, Any]]) -> tuple[int, int]:
    inserted = 0
    unchanged = 0
    with connect() as connection:
        for event in events:
            for row in _event_rows(event, sport_key):
                if _insert_if_changed(connection, row):
                    inserted += 1
                else:
                    unchanged += 1
        connection.commit()
    return inserted, unchanged


def _batch_report(
    summary: dict[str, Any],
    *,
    fatal_error: BaseException | None = None,
) -> dict[str, Any]:
    errors = list(summary["errors"])
    report = {
        "trigger": summary["trigger"],
        "sports_planned": summary["sports_planned"],
        "sports_attempted": summary["sports_attempted"],
        "sports_succeeded": summary["sports_succeeded"],
        "sport_keys": summary["sport_keys"],
        "quota_remaining": summary["quota_remaining"],
        "quota_verified": summary["quota_verified"],
        "stop_reason": summary["stop_reason"],
        "errors": errors,
    }
    if fatal_error is not None:
        fatal_text = f"{type(fatal_error).__name__}: {fatal_error}"[:500]
        report["fatal_error"] = fatal_text
        report["errors"] = [*errors, fatal_text]
    return report


def _checkpoint_batch(batch_id: int, summary: dict[str, Any]) -> None:
    """Persist quota/progress while the batch remains running."""

    with connect() as connection:
        connection.execute(
            """
            UPDATE raw.ingestion_batches
            SET report = %s, record_count = %s
            WHERE batch_id = %s
            """,
            (
                Jsonb(_batch_report(summary)),
                summary["snapshots_inserted"],
                batch_id,
            ),
        )
        connection.commit()


def _finish_batch(batch_id: int, summary: dict[str, Any]) -> None:
    report = _batch_report(summary)
    error_summary = "; ".join(summary["errors"])[:1000] or None
    with connect() as connection:
        connection.execute(
            """
            UPDATE raw.ingestion_batches
            SET finished_at_utc = now(), record_count = %s, status = %s,
                error_summary = %s, report = %s
            WHERE batch_id = %s
            """,
            (
                summary["snapshots_inserted"],
                summary["status"],
                error_summary,
                Jsonb(report),
                batch_id,
            ),
        )
        connection.commit()


def _fail_batch_best_effort(
    batch_id: int,
    error: BaseException,
    summary: dict[str, Any],
) -> None:
    terminal_status = "partial" if summary["sports_succeeded"] > 0 else "failed"
    report = _batch_report(summary, fatal_error=error)
    error_summary = "; ".join(report["errors"])[:1000]
    try:
        with connect() as connection:
            connection.execute(
                """
                UPDATE raw.ingestion_batches
                SET finished_at_utc = now(), status = %s, record_count = %s,
                    error_summary = %s, report = %s
                WHERE batch_id = %s AND status = 'running'
                """,
                (
                    terminal_status,
                    summary["snapshots_inserted"],
                    error_summary,
                    Jsonb(report),
                    batch_id,
                ),
            )
            connection.commit()
    except Exception:
        # The original database exception remains the actionable failure. A stale
        # running batch is treated as an in-flight/backoff signal by the gate.
        pass


def _quota_int(value: str | None) -> int | None:
    try:
        parsed = int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is not None and 0 <= parsed <= 999_999_999 else None


def collect_consensus_once(
    *,
    trigger: str = "manual",
    sport_keys: Sequence[str] | None = None,
    provider_quota_remaining: int | None = None,
    provider_quota_verified: bool | None = None,
) -> dict[str, Any]:
    selected_keys = list(sport_keys) if sport_keys is not None else active_sport_keys()
    summary: dict[str, Any] = {
        "status": "failed",
        "trigger": trigger,
        "sports_planned": 0,
        "sports_attempted": 0,
        "sports_succeeded": 0,
        "sports_queried": 0,
        "events_seen": 0,
        "snapshots_inserted": 0,
        "snapshots_unchanged": 0,
        "quota_remaining": provider_quota_remaining,
        "quota_verified": (
            True
            if provider_quota_remaining is not None and provider_quota_verified is None
            else provider_quota_verified
        ),
        "unmapped_leagues": None,
        "sport_keys": [],
        "stop_reason": None,
        "errors": [],
    }
    real_keys = [key for key in selected_keys if not key.startswith("__unmapped__:")]
    for key in selected_keys:
        if key.startswith("__unmapped__:"):
            summary["unmapped_leagues"] = key.split(":", 1)[1]
    summary["sports_planned"] = len(real_keys)
    summary["sport_keys"] = real_keys
    if not real_keys:
        summary["status"] = "skipped"
        summary["warning"] = "竞彩在售联赛均未映射到 sport key, 未发起请求"
        return summary

    monthly_budget = _env_int("ODDS_API_MONTHLY_REQUEST_BUDGET", 400, minimum=0)
    quota_reserve = _env_int("ODDS_API_QUOTA_RESERVE", 100)
    validate_provider_configuration()
    if summary["quota_remaining"] is None or summary["quota_verified"] is None:
        latest_remaining, latest_verified = _latest_quota_observation()
        if summary["quota_remaining"] is None:
            summary["quota_remaining"] = latest_remaining
        if summary["quota_verified"] is None:
            summary["quota_verified"] = latest_verified
    if summary["quota_verified"] is False:
        summary["status"] = "skipped"
        summary["stop_reason"] = "provider_quota_unknown"
        return summary
    batch_id = _start_batch(trigger, real_keys)
    summary["batch_id"] = batch_id

    try:
        for sport_key in real_keys:
            if (
                summary["quota_verified"] is not False
                and summary["quota_remaining"] is not None
                and summary["quota_remaining"] <= quota_reserve
            ):
                summary["stop_reason"] = "provider_quota_reserve"
                break
            reservation = _reserve_request(batch_id, monthly_budget)
            if reservation != "reserved":
                summary["stop_reason"] = reservation
                break

            summary["sports_attempted"] += 1
            try:
                events, remaining_raw = fetch_sport_odds(sport_key)
            except Exception as error:
                message = f"{sport_key}: {type(error).__name__}: {error}"[:500]
                summary["errors"].append(message)
                status_code = getattr(error, "status_code", None)
                parsed_remaining = _quota_int(getattr(error, "quota_remaining", None))
                if parsed_remaining is not None:
                    summary["quota_remaining"] = parsed_remaining
                    summary["quota_verified"] = True
                elif status_code == 429:
                    # A rate-limit response without a usable header must fail closed.
                    summary["quota_remaining"] = 0
                    summary["quota_verified"] = True
                else:
                    # The reserved request may have consumed provider quota. Never
                    # carry a stale remaining value across runs without a new header.
                    summary["quota_verified"] = False
                if status_code in (401, 403, 429) or any(
                    code in str(error) for code in ("HTTP 401", "HTTP 403", "HTTP 429")
                ):
                    summary["stop_reason"] = "provider_rejected_requests"
                elif isinstance(error, ProviderRequestError):
                    summary["stop_reason"] = "provider_response_error"
                else:
                    summary["stop_reason"] = "provider_request_error"
                _checkpoint_batch(batch_id, summary)
                break

            parsed_remaining = _quota_int(remaining_raw)
            if parsed_remaining is None:
                summary["stop_reason"] = "provider_quota_unknown"
                summary["quota_verified"] = False
            else:
                summary["quota_remaining"] = parsed_remaining
                summary["quota_verified"] = True
            _checkpoint_batch(batch_id, summary)
            try:
                inserted, unchanged = _store_sport_events(sport_key, events)
            except PsycopgError:
                raise
            except Exception as error:
                summary["errors"].append(
                    f"{sport_key}: persistence {type(error).__name__}: {error}"[:500]
                )
                summary["stop_reason"] = "persistence_error"
                break
            summary["sports_succeeded"] += 1
            summary["sports_queried"] += 1
            summary["events_seen"] += len(events)
            summary["snapshots_inserted"] += inserted
            summary["snapshots_unchanged"] += unchanged
            _checkpoint_batch(batch_id, summary)
            if summary["stop_reason"] == "provider_quota_unknown":
                break

        if (
            summary["sports_succeeded"] == summary["sports_planned"]
            and not summary["errors"]
            and not summary["stop_reason"]
        ):
            summary["status"] = "succeeded"
        elif summary["sports_succeeded"] > 0:
            summary["status"] = "partial"
        else:
            summary["status"] = "failed"
        _finish_batch(batch_id, summary)
    except Exception as error:
        _fail_batch_best_effort(batch_id, error, summary)
        raise
    return summary
