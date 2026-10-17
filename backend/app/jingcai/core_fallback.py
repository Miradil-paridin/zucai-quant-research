"""Import current Sporttery selections into ``core`` as an isolated fallback.

The fallback supplies only scheduled fixtures selected for sale. It never
claims full competition coverage and never overwrites status or scores already
provided by a general-purpose fixture source.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Mapping

from .matching import jingcai_kickoff_utc
from .team_names import JC_TO_CONSENSUS
from ..database import connect
from ..repository import Repository, repository


SOURCE = "sporttery"
RESOURCE_TYPE = "core_fixture_fallback"

LEAGUE_COUNTRIES = {
    "英格兰超级联赛": "England",
    "英格兰冠军联赛": "England",
    "西班牙甲级联赛": "Spain",
    "德国甲级联赛": "Germany",
    "意大利甲级联赛": "Italy",
    "法国甲级联赛": "France",
    "瑞典超级联赛": "Sweden",
    "挪威超级联赛": "Norway",
    "芬兰超级联赛": "Finland",
    "日本职业联赛": "Japan",
    "韩国职业联赛": "South Korea",
    "美国职业大联盟": "USA",
    "荷兰甲级联赛": "Netherlands",
    "葡萄牙超级联赛": "Portugal",
    "巴西甲级联赛": "Brazil",
}

CANONICAL_COMPETITION_CODES = {
    "欧洲冠军联赛": "CL",
    "英格兰超级联赛": "PL",
    "西班牙甲级联赛": "PD",
    "德国甲级联赛": "BL1",
    "意大利甲级联赛": "SA",
    "法国甲级联赛": "FL1",
    "瑞典超级联赛": "SE1",
    "挪威超级联赛": "NO1",
    "芬兰超级联赛": "FI1",
}


def normalize_jingcai_fixture(row: Mapping[str, Any]) -> dict[str, Any] | None:
    kickoff = jingcai_kickoff_utc(
        row.get("match_date_local"),
        row.get("match_time_local"),
    )
    if kickoff is None:
        return None
    league_name = str(row.get("league_name") or "竞彩未分类赛事")
    league_code = str(row.get("league_code") or "").strip()
    stable_suffix = league_code or hashlib.sha1(league_name.encode("utf-8")).hexdigest()[:10]
    canonical_code = CANONICAL_COMPETITION_CODES.get(
        league_name,
        f"JC-{stable_suffix.upper()}",
    )
    country = LEAGUE_COUNTRIES.get(league_name, "World")
    home = str(row["home_team"])
    away = str(row["away_team"])
    official_id = str(row["official_match_id"])
    season_year = row["match_date_local"].year
    return {
        "fixture_id": f"jc-{official_id}",
        "provider_ids": {SOURCE: official_id},
        "source": SOURCE,
        "coverage": "selected_for_sale",
        "competition": league_name,
        "competition_original": league_name,
        "competition_code": league_code or canonical_code,
        "canonical_competition_code": canonical_code,
        "country": country,
        "country_original": country,
        "round": None,
        "round_original": None,
        "season_label": str(season_year),
        "season_start": None,
        "season_end": None,
        "kickoff_utc": kickoff.isoformat(),
        "status": "scheduled",
        "status_detail": "Sporttery sale listing; schedule only",
        "elapsed": None,
        "venue": None,
        "city": None,
        "home_team": {
            "id": home,
            "name": home,
            "name_original": JC_TO_CONSENSUS.get(home, home),
            "short_name": home,
            "crest": None,
        },
        "away_team": {
            "id": away,
            "name": away,
            "name_original": JC_TO_CONSENSUS.get(away, away),
            "short_name": away,
            "crest": None,
        },
        "score": {"home": None, "away": None},
    }


def ingest_current_fallback(
    data_repository: Repository = repository,
    *,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    current = now_utc or datetime.now(UTC)
    batch_id = data_repository.start_batch(SOURCE, RESOURCE_TYPE)
    try:
        with connect() as connection:
            rows = connection.execute(
                """
                SELECT official_match_id, league_code, league_name, home_team,
                       away_team, match_date_local, match_time_local, raw_payload
                FROM jingcai.jc_matches
                WHERE last_seen_at_utc >= now() - interval '2 days'
                  AND match_date_local BETWEEN
                      (now() AT TIME ZONE 'Asia/Shanghai')::date
                      AND (now() AT TIME ZONE 'Asia/Shanghai')::date + 7
                ORDER BY match_date_local, match_time_local, official_match_id
                """
            ).fetchall()
            fixtures = [
                item
                for item in (normalize_jingcai_fixture(row) for row in rows)
                if item is not None
                and datetime.fromisoformat(item["kickoff_utc"]) >= current
            ]
            data_repository.store_raw_payload(
                connection,
                batch_id=batch_id,
                source_name=SOURCE,
                resource_type=RESOURCE_TYPE,
                payload={
                    "coverage": "selected_for_sale",
                    "fixtures": [dict(row["raw_payload"] or {}) for row in rows],
                },
                source_entity_id="active-sale-window",
                event_time_utc=current,
            )
            for fixture in fixtures:
                data_repository.upsert_fixture(connection, fixture, SOURCE)
            connection.commit()
        data_repository.finish_batch(
            batch_id,
            request_count=0,
            record_count=len(fixtures),
            status="succeeded",
        )
        return {
            "source": SOURCE,
            "coverage": "selected_for_sale",
            "fixtures_seen": len(rows),
            "fixtures_upserted": len(fixtures),
        }
    except Exception as error:
        data_repository.fail_batch(batch_id, error)
        raise
