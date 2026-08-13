"""国际共识采集的智能门控: 把免费额度集中在开赛前窗口。

规则(脚本每小时被计划任务唤起一次, 由本模块决定是否真正发请求):
- 未来 5 小时内有竞彩场次停售, 且距上次采集 >= 90 分钟 -> 采集(kickoff_window)
- 距上次采集 >= 24 小时 -> 采集(daily_heartbeat)
- 其余情况跳过, 不消耗额度。
预算约 300-400 请求/月, 在免费 500 内。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

BEIJING = timezone(timedelta(hours=8))
KICKOFF_LOOKAHEAD_HOURS = 5.0
KICKOFF_MIN_GAP_HOURS = 1.5
HEARTBEAT_HOURS = 23.0  # 略小于24, 避免整点任务逐日漂移


def _parse_beijing(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = raw.strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=BEIJING)
        except ValueError:
            continue
    return None


def should_collect_now(connection: Any) -> tuple[bool, str]:
    row = connection.execute(
        "SELECT max(captured_at_utc) AS last FROM jingcai.consensus_odds_snapshots"
    ).fetchone()
    last = row["last"]
    now_utc = datetime.now(timezone.utc)
    hours_since = 1e9 if last is None else (now_utc - last).total_seconds() / 3600.0

    kickoff_rows = connection.execute(
        """
        SELECT match_date_local, match_time_local, sell_end_local
        FROM jingcai.jc_matches
        WHERE last_seen_at_utc > now() - interval '3 days'
        """
    ).fetchall()
    horizon = now_utc + timedelta(hours=KICKOFF_LOOKAHEAD_HOURS)
    upcoming = False
    for row in kickoff_rows:
        moment = _parse_beijing(row["sell_end_local"])
        if moment is None and row["match_date_local"] and row["match_time_local"]:
            moment = _parse_beijing(
                f"{row['match_date_local']} {row['match_time_local']}"
            )
        if moment and now_utc <= moment.astimezone(timezone.utc) <= horizon:
            upcoming = True
            break

    if upcoming and hours_since >= KICKOFF_MIN_GAP_HOURS:
        return True, "kickoff_window"
    if hours_since >= HEARTBEAT_HOURS:
        return True, "daily_heartbeat"
    return False, f"skip(last {hours_since:.1f}h ago, upcoming={upcoming})"
