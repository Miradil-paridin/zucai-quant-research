#!/usr/bin/env python3
"""Cross-check football-data.co.uk scores against two independent sources.

Primary source:
  football-data.co.uk CSV, 2024/25 top five leagues.

Independent sources:
  football-data.org API snapshots for all 1,752 fixtures and scores.
  OpenFootball JSON as a second corroborating source for every fixture
  identity and 1,732 scores (its Serie A/La Liga final rounds are incomplete).

No source file is modified and no team mapping is guessed at runtime. Every
alias is an explicit, reviewable mapping below.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


VERSION = "1.0.0"
OPENFOOTBALL_FILES = {
    "D1": "de.1.json",
    "E0": "en.1.json",
    "F1": "fr.1.json",
    "I1": "it.1.json",
    "SP1": "es.1.json",
}

# Reference name -> exact football-data.co.uk name.
TEAM_ALIASES: dict[str, dict[str, str]] = {
    "D1": {
        "1. FC Heidenheim 1846": "Heidenheim",
        "1. FC Union Berlin": "Union Berlin",
        "1. FSV Mainz 05": "Mainz",
        "Bayer 04 Leverkusen": "Leverkusen",
        "Borussia Dortmund": "Dortmund",
        "Borussia Mönchengladbach": "M'gladbach",
        "Eintracht Frankfurt": "Ein Frankfurt",
        "FC Augsburg": "Augsburg",
        "FC Bayern München": "Bayern Munich",
        "FC St. Pauli 1910": "St Pauli",
        "Holstein Kiel": "Holstein Kiel",
        "RB Leipzig": "RB Leipzig",
        "SC Freiburg": "Freiburg",
        "SV Werder Bremen": "Werder Bremen",
        "TSG 1899 Hoffenheim": "Hoffenheim",
        "VfB Stuttgart": "Stuttgart",
        "VfL Bochum 1848": "Bochum",
        "VfL Wolfsburg": "Wolfsburg",
    },
    "E0": {
        "AFC Bournemouth": "Bournemouth",
        "Arsenal FC": "Arsenal",
        "Aston Villa FC": "Aston Villa",
        "Brentford FC": "Brentford",
        "Brighton & Hove Albion FC": "Brighton",
        "Chelsea FC": "Chelsea",
        "Crystal Palace FC": "Crystal Palace",
        "Everton FC": "Everton",
        "Fulham FC": "Fulham",
        "Ipswich Town FC": "Ipswich",
        "Leicester City FC": "Leicester",
        "Liverpool FC": "Liverpool",
        "Manchester City FC": "Man City",
        "Manchester United FC": "Man United",
        "Newcastle United FC": "Newcastle",
        "Nottingham Forest FC": "Nott'm Forest",
        "Southampton FC": "Southampton",
        "Tottenham Hotspur FC": "Tottenham",
        "West Ham United FC": "West Ham",
        "Wolverhampton Wanderers FC": "Wolves",
    },
    "F1": {
        "AJ Auxerre": "Auxerre",
        "AS Monaco FC": "Monaco",
        "AS Saint-Étienne": "St Etienne",
        "Angers SCO": "Angers",
        "FC Nantes": "Nantes",
        "Le Havre AC": "Le Havre",
        "Lille OSC": "Lille",
        "Montpellier HSC": "Montpellier",
        "OGC Nice": "Nice",
        "Olympique Lyonnais": "Lyon",
        "Olympique de Marseille": "Marseille",
        "Paris Saint-Germain FC": "Paris SG",
        "RC Strasbourg Alsace": "Strasbourg",
        "Racing Club de Lens": "Lens",
        "Stade Brestois 29": "Brest",
        "Stade Rennais FC 1901": "Rennes",
        "Stade de Reims": "Reims",
        "Toulouse FC": "Toulouse",
    },
    "I1": {
        "AC Milan": "Milan",
        "AC Monza": "Monza",
        "ACF Fiorentina": "Fiorentina",
        "AS Roma": "Roma",
        "Atalanta BC": "Atalanta",
        "Bologna FC 1909": "Bologna",
        "Cagliari Calcio": "Cagliari",
        "Como 1907": "Como",
        "Empoli FC": "Empoli",
        "FC Internazionale Milano": "Inter",
        "Genoa CFC": "Genoa",
        "Hellas Verona FC": "Verona",
        "Juventus FC": "Juventus",
        "Parma Calcio 1913": "Parma",
        "SS Lazio": "Lazio",
        "SSC Napoli": "Napoli",
        "Torino FC": "Torino",
        "US Lecce": "Lecce",
        "Udinese Calcio": "Udinese",
        "Venezia FC": "Venezia",
    },
    "SP1": {
        "Athletic Club": "Ath Bilbao",
        "CA Osasuna": "Osasuna",
        "CD Leganés": "Leganes",
        "Club Atlético de Madrid": "Ath Madrid",
        "Deportivo Alavés": "Alaves",
        "FC Barcelona": "Barcelona",
        "Getafe CF": "Getafe",
        "Girona FC": "Girona",
        "RC Celta de Vigo": "Celta",
        "RCD Espanyol de Barcelona": "Espanol",
        "RCD Mallorca": "Mallorca",
        "Rayo Vallecano de Madrid": "Vallecano",
        "Real Betis Balompié": "Betis",
        "Real Madrid CF": "Real Madrid",
        "Real Sociedad de Fútbol": "Sociedad",
        "Real Valladolid CF": "Valladolid",
        "Sevilla FC": "Sevilla",
        "UD Las Palmas": "Las Palmas",
        "Valencia CF": "Valencia",
        "Villarreal CF": "Villarreal",
    },
}

FOOTBALL_DATA_ORG_FILES = {
    "D1": "BL1-season-2024.json",
    "E0": "PL-season-2024.json",
    "F1": "FL1-season-2024.json",
    "I1": "SA-season-2024.json",
    "SP1": "PD-season-2024.json",
}


@dataclass
class Finding:
    severity: str
    code: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def add(
    findings: list[Finding],
    severity: str,
    code: str,
    message: str,
    **evidence: Any,
) -> None:
    findings.append(Finding(severity, code, message, evidence))


def manifest_entries(root: Path, findings: list[Finding]) -> dict[str, dict[str, Any]]:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        add(findings, "fail", "manifest.missing", "Source manifest is missing.", root=str(root))
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        add(
            findings,
            "fail",
            "manifest.invalid",
            "Source manifest cannot be parsed.",
            root=str(root),
            error=str(error),
        )
        return {}
    entries = {}
    for entry in payload.get("files", []):
        name = entry.get("file")
        if name:
            entries[str(name).casefold()] = entry
    return entries


def verify_manifest_file(
    root: Path,
    filename: str,
    entries: dict[str, dict[str, Any]],
    findings: list[Finding],
) -> None:
    path = root / filename
    entry = entries.get(filename.casefold())
    if entry is None:
        add(
            findings,
            "fail",
            "manifest.file_missing",
            "Manifest has no entry for an audited file.",
            path=str(path),
        )
        return
    if not path.is_file():
        add(findings, "fail", "source.file_missing", "Audited file is absent.", path=str(path))
        return
    expected_hash = str(entry.get("sha256", "")).casefold()
    actual_hash = file_sha256(path)
    if expected_hash != actual_hash:
        add(
            findings,
            "fail",
            "manifest.hash_mismatch",
            "Audited file differs from its manifest SHA-256.",
            path=str(path),
            expected=expected_hash,
            actual=actual_hash,
        )
    if entry.get("bytes") is not None and int(entry["bytes"]) != path.stat().st_size:
        add(
            findings,
            "fail",
            "manifest.size_mismatch",
            "Audited file size differs from its manifest.",
            path=str(path),
            expected=int(entry["bytes"]),
            actual=path.stat().st_size,
        )
    parsed = urlparse(str(entry.get("url", "")))
    if parsed.scheme != "https":
        add(
            findings,
            "fail",
            "manifest.url_invalid",
            "Reference source URL must use HTTPS.",
            path=str(path),
            url=entry.get("url"),
        )


def read_primary(
    root: Path, findings: list[Finding]
) -> tuple[
    dict[str, dict[tuple[str, str, str], tuple[int, int]]],
    dict[str, dict[tuple[str, str, str], dict[str, str]]],
]:
    scores: dict[str, dict[tuple[str, str, str], tuple[int, int]]] = {}
    raw_rows: dict[str, dict[tuple[str, str, str], dict[str, str]]] = {}
    entries = manifest_entries(root, findings)
    for div in OPENFOOTBALL_FILES:
        filename = f"{div}.csv"
        verify_manifest_file(root, filename, entries, findings)
        path = root / filename
        league_scores = {}
        league_rows = {}
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row_number, row in enumerate(csv.DictReader(handle), start=2):
                try:
                    parsed_date = datetime.strptime(row["Date"], "%d/%m/%Y").date().isoformat()
                    key = (parsed_date, row["HomeTeam"], row["AwayTeam"])
                    score = (int(row["FTHG"]), int(row["FTAG"]))
                except (KeyError, ValueError) as error:
                    add(
                        findings,
                        "fail",
                        "primary.row_invalid",
                        "Primary match identity or score is invalid.",
                        path=str(path),
                        row=row_number,
                        error=str(error),
                    )
                    continue
                if key in league_scores:
                    add(
                        findings,
                        "fail",
                        "primary.duplicate",
                        "Primary match identity is duplicated.",
                        div=div,
                        key=key,
                    )
                league_scores[key] = score
                league_rows[key] = row
        scores[div] = league_scores
        raw_rows[div] = league_rows
    return scores, raw_rows


def read_openfootball(
    root: Path,
    primary: dict[str, dict[tuple[str, str, str], tuple[int, int]]],
    primary_rows: dict[str, dict[tuple[str, str, str], dict[str, str]]],
    findings: list[Finding],
) -> tuple[dict[str, dict[str, int]], set[tuple[str, tuple[str, str, str]]], dict[str, Any]]:
    entries = manifest_entries(root, findings)
    league_results: dict[str, dict[str, int]] = {}
    unresolved: set[tuple[str, tuple[str, str, str]]] = set()
    union_award: dict[str, Any] = {"found": False}

    for div, filename in OPENFOOTBALL_FILES.items():
        verify_manifest_file(root, filename, entries, findings)
        payload = json.loads((root / filename).read_text(encoding="utf-8"))
        matches = payload.get("matches")
        if not isinstance(matches, list):
            add(
                findings,
                "fail",
                "openfootball.matches_missing",
                "OpenFootball file has no matches list.",
                div=div,
                file=filename,
            )
            continue

        mapping = TEAM_ALIASES[div]
        reference_teams = {
            str(match.get(side, "")) for match in matches for side in ("team1", "team2")
        }
        unmapped = sorted(reference_teams - set(mapping))
        if unmapped:
            add(
                findings,
                "fail",
                "mapping.reference_team_unmapped",
                "OpenFootball team has no explicit alias mapping.",
                div=div,
                teams=unmapped,
            )
        mapped_values = list(mapping.values())
        if len(mapped_values) != len(set(mapped_values)):
            add(
                findings,
                "fail",
                "mapping.not_bijective",
                "Multiple reference team names map to one primary team.",
                div=div,
            )
        primary_teams = {
            team for key in primary[div] for team in (key[1], key[2])
        }
        if set(mapped_values) != primary_teams:
            add(
                findings,
                "fail",
                "mapping.primary_team_set_mismatch",
                "Explicit team aliases do not cover the primary league team set exactly.",
                div=div,
                missing=sorted(primary_teams - set(mapped_values)),
                extra=sorted(set(mapped_values) - primary_teams),
            )

        counters = Counter(
            {
                "reference_fixtures": len(matches),
                "identity_matches": 0,
                "score_matches": 0,
                "score_mismatches": 0,
                "score_missing_in_reference": 0,
                "halftime_comparable": 0,
                "halftime_matches": 0,
                "halftime_mismatches": 0,
                "halftime_missing_in_either_source": 0,
            }
        )
        seen_keys = set()
        for position, match in enumerate(matches, start=1):
            home_reference = str(match.get("team1", ""))
            away_reference = str(match.get("team2", ""))
            if home_reference not in mapping or away_reference not in mapping:
                continue
            key = (
                str(match.get("date", "")),
                mapping[home_reference],
                mapping[away_reference],
            )
            if key in seen_keys:
                add(
                    findings,
                    "fail",
                    "openfootball.duplicate",
                    "OpenFootball fixture identity is duplicated.",
                    div=div,
                    key=key,
                )
            seen_keys.add(key)
            expected = primary[div].get(key)
            if expected is None:
                add(
                    findings,
                    "fail",
                    "crosscheck.fixture_missing",
                    "OpenFootball fixture is absent from the primary CSV after explicit alias mapping.",
                    div=div,
                    key=key,
                )
                continue
            counters["identity_matches"] += 1
            score = match.get("score") or {}
            full_time = score.get("ft") if isinstance(score, dict) else None
            if not isinstance(full_time, list) or len(full_time) != 2:
                counters["score_missing_in_reference"] += 1
                unresolved.add((div, key))
                continue
            actual = (int(full_time[0]), int(full_time[1]))
            if actual == expected:
                counters["score_matches"] += 1
            else:
                counters["score_mismatches"] += 1
                add(
                    findings,
                    "fail",
                    "crosscheck.score_mismatch",
                    "OpenFootball score differs from football-data.co.uk.",
                    div=div,
                    key=key,
                    primary=expected,
                    reference=actual,
                )

            primary_row = primary_rows[div][key]
            primary_ht = (primary_row.get("HTHG", ""), primary_row.get("HTAG", ""))
            reference_ht = score.get("ht") if isinstance(score, dict) else None
            if (
                primary_ht[0] != ""
                and primary_ht[1] != ""
                and isinstance(reference_ht, list)
                and len(reference_ht) == 2
            ):
                counters["halftime_comparable"] += 1
                expected_ht = (int(primary_ht[0]), int(primary_ht[1]))
                actual_ht = (int(reference_ht[0]), int(reference_ht[1]))
                if expected_ht == actual_ht:
                    counters["halftime_matches"] += 1
                else:
                    counters["halftime_mismatches"] += 1
                    add(
                        findings,
                        "warn",
                        "crosscheck.openfootball_halftime_mismatch",
                        "OpenFootball half-time score differs from football-data.co.uk.",
                        div=div,
                        key=key,
                        primary=expected_ht,
                        reference=actual_ht,
                    )
            else:
                counters["halftime_missing_in_either_source"] += 1

            if div == "D1" and key == ("2024-12-14", "Union Berlin", "Bochum"):
                union_award = {
                    "found": True,
                    "primary_score": expected,
                    "openfootball_score": actual,
                    "openfootball_status": match.get("status"),
                    "reference_file": str((root / filename).resolve()),
                }
        league_results[div] = dict(counters)
    return league_results, unresolved, union_award


def read_football_data_org(
    root: Path,
    primary: dict[str, dict[tuple[str, str, str], tuple[int, int]]],
    primary_rows: dict[str, dict[tuple[str, str, str], dict[str, str]]],
    unresolved: set[tuple[str, tuple[str, str, str]]],
    findings: list[Finding],
) -> dict[str, dict[str, Any]]:
    entries = manifest_entries(root, findings)
    output: dict[str, dict[str, int]] = {}
    for div, filename in FOOTBALL_DATA_ORG_FILES.items():
        verify_manifest_file(root, filename, entries, findings)
        payload = json.loads((root / filename).read_text(encoding="utf-8"))
        matches = payload.get("matches")
        if not isinstance(matches, list):
            add(
                findings,
                "fail",
                "football_data_org.matches_missing",
                "football-data.org response has no matches list.",
                div=div,
            )
            continue
        mapping = TEAM_ALIASES[div]
        counters = Counter(
            {
                "reference_fixtures": len(matches),
                "identity_matches": 0,
                "resolved_openfootball_gaps": 0,
                "score_matches": 0,
                "score_mismatches": 0,
                "halftime_comparable": 0,
                "halftime_matches": 0,
                "halftime_mismatches": 0,
                "halftime_missing_in_either_source": 0,
            }
        )
        awarded_matches = []
        for match in matches:
            home = str((match.get("homeTeam") or {}).get("name", ""))
            away = str((match.get("awayTeam") or {}).get("name", ""))
            if home not in mapping or away not in mapping:
                add(
                    findings,
                    "fail",
                    "mapping.football_data_org_team_unmapped",
                    "football-data.org team has no explicit alias mapping.",
                    div=div,
                    home=home,
                    away=away,
                )
                continue
            key = (str(match.get("utcDate", ""))[:10], mapping[home], mapping[away])
            expected = primary[div].get(key)
            if expected is None:
                add(
                    findings,
                    "fail",
                    "football_data_org.fixture_missing",
                    "football-data.org fixture is absent from the primary CSV.",
                    div=div,
                    key=key,
                )
                continue
            counters["identity_matches"] += 1
            if match.get("status") == "AWARDED":
                awarded_matches.append(
                    {"date": key[0], "home": key[1], "away": key[2]}
                )
            if (div, key) in unresolved:
                counters["resolved_openfootball_gaps"] += 1
            full_time = ((match.get("score") or {}).get("fullTime") or {})
            try:
                actual = (int(full_time["home"]), int(full_time["away"]))
            except (KeyError, TypeError, ValueError) as error:
                add(
                    findings,
                    "fail",
                    "football_data_org.score_missing",
                    "football-data.org reference score is absent or invalid.",
                    div=div,
                    key=key,
                    error=str(error),
                )
                continue
            if actual == expected:
                counters["score_matches"] += 1
                unresolved.discard((div, key))
            else:
                counters["score_mismatches"] += 1
                add(
                    findings,
                    "fail",
                    "crosscheck.score_mismatch",
                    "football-data.org score differs from football-data.co.uk.",
                    div=div,
                    key=key,
                    primary=expected,
                    reference=actual,
                )

            primary_row = primary_rows[div][key]
            primary_ht = (primary_row.get("HTHG", ""), primary_row.get("HTAG", ""))
            half_time = ((match.get("score") or {}).get("halfTime") or {})
            reference_ht = (half_time.get("home"), half_time.get("away"))
            if (
                primary_ht[0] != ""
                and primary_ht[1] != ""
                and reference_ht[0] is not None
                and reference_ht[1] is not None
            ):
                counters["halftime_comparable"] += 1
                expected_ht = (int(primary_ht[0]), int(primary_ht[1]))
                actual_ht = (int(reference_ht[0]), int(reference_ht[1]))
                if expected_ht == actual_ht:
                    counters["halftime_matches"] += 1
                else:
                    counters["halftime_mismatches"] += 1
                    add(
                        findings,
                        "warn",
                        "crosscheck.football_data_org_halftime_mismatch",
                        "football-data.org half-time score differs from football-data.co.uk.",
                        div=div,
                        key=key,
                        primary=expected_ht,
                        reference=actual_ht,
                    )
            else:
                counters["halftime_missing_in_either_source"] += 1
        output[div] = {**dict(counters), "awarded_matches": awarded_matches}
    return output


def verify_official_evidence(root: Path | None, findings: list[Finding]) -> dict[str, Any]:
    if root is None:
        return {"status": "not_supplied"}
    entries = manifest_entries(root, findings)
    requirements = {
        "inter-fiorentina-2025-02-06.html": (
            "Half Time, Fiorentina 0-0 Inter",
            "Fiorentina–Inter half-time 0-0",
        ),
        "dfb-union-bochum-award.html": (
            "mit 2:0 Toren für Bochum",
            "Union Berlin–Bochum awarded 0-2",
        ),
        "dfb-union-bochum-datencenter.html": (
            "0:2 Wert.",
            "DFB Datencenter awarded-result marker",
        ),
        "ligue1-montpellier-st-etienne-abandoned.html": (
            "57e minute",
            "Montpellier–Saint-Etienne stopped in minute 57",
        ),
    }
    checks = []
    for filename, (needle, fact) in requirements.items():
        verify_manifest_file(root, filename, entries, findings)
        path = root / filename
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        matched = needle in text
        checks.append(
            {
                "file": str(path.resolve()),
                "sha256": file_sha256(path) if path.is_file() else None,
                "fact": fact,
                "evidence_text_found": matched,
            }
        )
        if not matched:
            add(
                findings,
                "fail",
                "official_evidence.text_missing",
                "Expected primary-source evidence text was not found.",
                file=str(path),
                fact=fact,
            )
    return {"status": "pass" if all(item["evidence_text_found"] for item in checks) else "fail", "checks": checks}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("primary_root")
    parser.add_argument("openfootball_root")
    parser.add_argument("football_data_org_root")
    parser.add_argument("--official-root")
    parser.add_argument("--output")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = parse_args(argv)
    primary_root = Path(args.primary_root).resolve()
    openfootball_root = Path(args.openfootball_root).resolve()
    football_data_org_root = Path(args.football_data_org_root).resolve()
    findings: list[Finding] = []

    primary, primary_rows = read_primary(primary_root, findings)
    openfootball, unresolved, union_award = read_openfootball(
        openfootball_root, primary, primary_rows, findings
    )
    football_data_org = read_football_data_org(
        football_data_org_root, primary, primary_rows, unresolved, findings
    )
    official_evidence = verify_official_evidence(
        Path(args.official_root).resolve() if args.official_root else None,
        findings,
    )

    union_key = ("2024-12-14", "Union Berlin", "Bochum")
    union_row = primary_rows.get("D1", {}).get(union_key, {})
    union_award["primary_null_halftime_preserved"] = all(
        union_row.get(field, "").strip() == "" for field in ("HTHG", "HTAG", "HTR")
    )
    union_award["primary_fulltime"] = (
        union_row.get("FTHG"),
        union_row.get("FTAG"),
        union_row.get("FTR"),
    )
    if not (
        union_award.get("found")
        and union_award.get("openfootball_status") == "awarded"
        and union_award.get("primary_score") == (0, 2)
        and union_award.get("openfootball_score") == (0, 2)
        and union_award["primary_null_halftime_preserved"]
    ):
        add(
            findings,
            "fail",
            "awarded_match.not_preserved",
            "The Union Berlin–Bochum awarded result/null half-time invariant failed.",
            **union_award,
        )

    if unresolved:
        add(
            findings,
            "fail",
            "crosscheck.unresolved_scores",
            "At least one primary score remains without independent confirmation.",
            matches=[
                {"div": div, "date": key[0], "home": key[1], "away": key[2]}
                for div, key in sorted(unresolved)
            ],
        )

    total_primary = sum(len(matches) for matches in primary.values())
    openfootball_identity = sum(
        item.get("identity_matches", 0) for item in openfootball.values()
    )
    openfootball_scores = sum(
        item.get("score_matches", 0) for item in openfootball.values()
    )
    football_data_org_scores = sum(
        item.get("score_matches", 0) for item in football_data_org.values()
    )
    football_data_org_identity = sum(
        item.get("identity_matches", 0) for item in football_data_org.values()
    )
    independent_confirmed = football_data_org_scores
    if openfootball_identity != total_primary:
        add(
            findings,
            "fail",
            "crosscheck.fixture_coverage_incomplete",
            "OpenFootball does not independently identify every primary fixture.",
            expected=total_primary,
            actual=openfootball_identity,
        )
    if independent_confirmed != total_primary:
        add(
            findings,
            "fail",
            "crosscheck.score_coverage_incomplete",
            "Independent sources do not confirm every primary score.",
            expected=total_primary,
            actual=independent_confirmed,
        )
    if football_data_org_identity != total_primary:
        add(
            findings,
            "fail",
            "crosscheck.football_data_org_fixture_coverage_incomplete",
            "football-data.org does not independently identify every primary fixture.",
            expected=total_primary,
            actual=football_data_org_identity,
        )

    counts = Counter(item.severity for item in findings)
    status = "fail" if counts["fail"] else "warn" if counts["warn"] else "pass"
    report = {
        "crosscheck_version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "policy": {
            "runtime_fuzzy_matching": False,
            "explicit_team_aliases_only": True,
            "source_files_modified": False,
            "missing_scores_imputed": False,
        },
        "sources": {
            "primary": str(primary_root),
            "openfootball": str(openfootball_root),
            "football_data_org": str(football_data_org_root),
        },
        "summary": {
            "primary_matches": total_primary,
            "openfootball_fixture_identities_matched": openfootball_identity,
            "openfootball_scores_matched": openfootball_scores,
            "football_data_org_fixture_identities_matched": football_data_org_identity,
            "football_data_org_scores_matched": football_data_org_scores,
            "football_data_org_resolved_openfootball_gaps": sum(
                item.get("resolved_openfootball_gaps", 0)
                for item in football_data_org.values()
            ),
            "independent_scores_confirmed": independent_confirmed,
            "unresolved_scores": len(unresolved),
            "findings": dict(counts),
        },
        "per_league_openfootball": openfootball,
        "football_data_org_full_season": football_data_org,
        "awarded_match_check": union_award,
        "official_evidence": official_evidence,
        "team_aliases": TEAM_ALIASES,
        "findings": [asdict(item) for item in findings],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 2 if status == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
