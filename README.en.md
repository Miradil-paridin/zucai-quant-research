# Football Quant Research Desk · 足彩量化研究台

[中文](README.md) · **English**

> **Archived (2026-10-18).** Verdict: with public data and the ~88% payout ratio of China Sports Lottery football (Jingcai), **no exploitable positive-EV edge was found**.
> This repository is an auditable research record. **It is not a betting tool and is not betting advice.** Full conclusions: [docs/final-report.md](docs/final-report.md).

## What it is

A local full-stack system for football quantitative research, built to answer one question: **can data make long-run money on Jingcai football betting?**

It ran three lines of research:

1. **In-house probability model** — rolling Poisson / Dixon-Coles with shot features and temperature calibration, compared against bookmaker prices in a strictly leakage-free walk-forward backtest.
2. **Historical spread backtest** — 7,051 matches pairing official Jingcai closing fixed odds (2015–2026) with international closing consensus, testing the cross-market spread.
3. **Live repricing-lag test** — Jingcai odds every 10 minutes plus on-demand sharp-book (Pinnacle etc.) odds, all timestamped, to test whether Jingcai repricing more slowly than the international market creates bettable mispricing.

## Results at a glance

| Track | Result |
|---|---|
| Model v1–v3 | Well calibrated (ECE ≈ 0.014) but worse than the market on Brier and log loss; when allowed any blend with opening odds, the optimal weight is **100% market / 0% model** |
| Jingcai vs international close (7,051 historical matches) | 13.5% Jingcai overround; EV>2% gives +6% ROI with t<1, EV>5% gives -8.3% |
| Live lag test (2.5 months, 1,593 aligned price pairs) | Mean EV -10.7% across all outcomes; 12 signals show a paper profit with ±81% standard error; apparent edges give back ~3.7 points before close; most signals have no single-match pool |

**In one line: the forecasts can match the market, but the ~12% margin means you would need to beat the market by more than 12% to profit. That gap is structural; a few more months of research will not close it.**

## Stack

- **Backend:** Python 3.14 · FastAPI · psycopg 3 · APScheduler · curl_cffi
- **Database:** PostgreSQL 16 (Docker), layered `raw` → `core`, every raw response and SHA-256 fingerprint retained
- **Frontend:** React 19 · Vite · ECharts (match desk, backtest, pre-match reference, data ops pages)
- **Tests:** pytest (141 passing), frontend smoke test

## Data sources

| Source | Used for | Key required |
|---|---|---|
| football-data.co.uk | Big-5 and Nordic historical results and odds, 1993/94–2025/26 | No |
| football-data.org | Fixtures, scores, cross-source verification | Yes |
| API-Football | Fixtures, lineups, injuries, events | Yes |
| The Odds API | Live international 1X2 odds | Yes |
| China Sports Lottery (sporttery.cn) | Jingcai on-sale odds and results | No (needs a mainland-China egress IP) |

The historical store holds 68,697 matches and 848,390 odds rows; 1,752 Big-5 matches from 2024/25 were verified against a second source.

## Layout

```text
backend/            FastAPI service, collectors, historical store, backtests, Jingcai analysis
  app/historical/   football-data.co.uk and Nordic historical imports
  app/research/     Rolling Poisson/DC model and walk-forward backtest
  app/jingcai/      Jingcai and consensus collectors, spread and repricing-lag analysis
  app/reference/    Pre-match reference page (de-vigged market probabilities + pre/post-match log)
  scripts/          One-off collection, import and backtest scripts
  tests/            pytest
frontend/           React + Vite + ECharts dashboard
docs/               Architecture, DB schema, backtest report, final report
scripts/            Historical data audits and the final audit final_live_audit.py
data/source_samples Hash-pinned samples for cross-source verification (no secrets)
```

## Running locally

### 1. Database

```powershell
docker compose up -d postgres
```

`docs/schema.sql` runs on first start. The database password in `docker-compose.yml` is a local development default and the port binds to `127.0.0.1` only; change the password before exposing it anywhere.

### 2. Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Fill in your own API_FOOTBALL_API_KEY, FOOTBALL_DATA_API_KEY and ODDS_API_KEY in .env
uvicorn app.main:app --reload --port 8000
```

**Keys live only in `backend/.env`, which is git-ignored. The repository contains no real keys.**

### 3. Frontend

```powershell
cd frontend
npm install
npm run dev -- --port 5174
```

### 4. Useful scripts

```powershell
cd backend
# Import Big-5 history (default 2021/22–2025/26; add --all-history to go back to 1993/94)
.\.venv\Scripts\python.exe scripts\import_historical.py
# Reproducible historical probability backtest
.\.venv\Scripts\python.exe scripts\run_historical_backtest.py
# Jingcai vs international close spread backtest
.\.venv\Scripts\python.exe scripts\run_jc_spread_backtest.py
# Final live repricing-lag audit
.\.venv\Scripts\python.exe ..\scripts\final_live_audit.py
# Tests
.\.venv\Scripts\python.exe -m pytest -q
```

Never run `docker compose down -v`; it deletes the database volume.

## Research principles

- **No leakage:** each prediction uses only earlier dates; all matches on a day are predicted before any result from that day is applied.
- **No invented data:** missing scores or odds timestamps stay missing; historical odds without per-row capture times are never used for ROI/CLV.
- **Honest reporting:** when the model loses to the market, the report says so; no synthetic profit curves.
- **Reproducible:** inputs, implementation and predictions carry SHA-256 fingerprints.

## Known issues (left unfixed at archive time)

- The international odds collection gate locks until the next month after a single network failure (see the final report).
- The Jingcai site's WAF blocks non-mainland egress IPs.

## Docs

- [Final report / 终结报告](docs/final-report.md)
- [Backtest report (Chinese)](docs/backtest-report.md)
- [Architecture](docs/architecture.md) · [Data dictionary](docs/data-dictionary.md) · [API](docs/api.md) · [Ingestion](docs/ingestion.md)
- [Pre-match reference system (Chinese)](docs/reference-system.md)

## Disclaimer

This project is for learning data engineering and statistical research only. Its own findings show that betting on Jingcai with public data has negative expected value. Do not use it for real betting.
