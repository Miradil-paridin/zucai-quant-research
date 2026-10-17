-- PostgreSQL 16+
-- 五大联赛量化研究系统核心 Schema

BEGIN;

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS audit;

CREATE TABLE IF NOT EXISTS raw.ingestion_batches (
    batch_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_name         text NOT NULL,
    resource_type       text NOT NULL,
    started_at_utc      timestamptz NOT NULL DEFAULT now(),
    finished_at_utc     timestamptz,
    cursor_value        text,
    request_count       integer NOT NULL DEFAULT 0 CHECK (request_count >= 0),
    record_count        integer NOT NULL DEFAULT 0 CHECK (record_count >= 0),
    status              text NOT NULL CHECK (status IN ('running','succeeded','partial','failed')),
    error_summary       text,
    report              jsonb NOT NULL DEFAULT '{}'::jsonb
);

ALTER TABLE raw.ingestion_batches
    ADD COLUMN IF NOT EXISTS report jsonb NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_ingestion_batches_source_resource_finished
    ON raw.ingestion_batches (source_name, resource_type, finished_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_ingestion_batches_source_resource_started
    ON raw.ingestion_batches (source_name, resource_type, started_at_utc DESC, batch_id DESC);

CREATE TABLE IF NOT EXISTS raw.source_payloads (
    payload_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id            bigint NOT NULL REFERENCES raw.ingestion_batches(batch_id),
    source_name         text NOT NULL,
    source_entity_id    text,
    resource_type       text NOT NULL,
    event_time_utc      timestamptz,
    ingested_at_utc     timestamptz NOT NULL DEFAULT now(),
    source_timezone     text,
    payload_hash        text NOT NULL,
    payload             jsonb NOT NULL,
    UNIQUE (source_name, resource_type, payload_hash)
);

CREATE INDEX IF NOT EXISTS idx_raw_payload_batch
    ON raw.source_payloads (batch_id, resource_type);
CREATE INDEX IF NOT EXISTS idx_raw_payload_event_time
    ON raw.source_payloads (event_time_utc DESC);

-- Immutable provenance for downloaded CSV/JSON source files. A URL may publish
-- multiple versions over time, therefore the content hash is part of identity.
CREATE TABLE IF NOT EXISTS raw.source_files (
    source_file_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_name         text NOT NULL,
    source_url          text NOT NULL,
    source_division     text,
    season_label        text,
    downloaded_at_utc   timestamptz NOT NULL DEFAULT now(),
    http_etag           text,
    http_last_modified  text,
    content_sha256      char(64) NOT NULL,
    content_bytes       bigint NOT NULL CHECK (content_bytes > 0),
    storage_path        text NOT NULL,
    parser_version      text NOT NULL,
    row_count           integer CHECK (row_count IS NULL OR row_count >= 0),
    status              text NOT NULL CHECK (status IN ('downloaded','validated','imported','rejected')),
    validation_report   jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (source_name, source_url, content_sha256)
);

CREATE INDEX IF NOT EXISTS idx_source_files_lookup
    ON raw.source_files (source_name, source_division, season_label, downloaded_at_utc DESC);

CREATE TABLE IF NOT EXISTS raw.historical_import_runs (
    import_run_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_name         text NOT NULL,
    started_at_utc      timestamptz NOT NULL DEFAULT now(),
    finished_at_utc     timestamptz,
    requested_files     integer NOT NULL DEFAULT 0 CHECK (requested_files >= 0),
    imported_files      integer NOT NULL DEFAULT 0 CHECK (imported_files >= 0),
    matches_upserted    integer NOT NULL DEFAULT 0 CHECK (matches_upserted >= 0),
    odds_upserted       integer NOT NULL DEFAULT 0 CHECK (odds_upserted >= 0),
    status              text NOT NULL CHECK (status IN ('running','succeeded','partial','failed')),
    error_summary       text,
    report              jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS core.competitions (
    competition_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code                text NOT NULL UNIQUE,
    name                text NOT NULL,
    country_code        char(2) NOT NULL,
    tier                smallint NOT NULL DEFAULT 1 CHECK (tier > 0),
    active              boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS core.seasons (
    season_id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    competition_id      bigint NOT NULL REFERENCES core.competitions(competition_id),
    label               text NOT NULL,
    starts_on           date NOT NULL,
    ends_on             date NOT NULL,
    UNIQUE (competition_id, label),
    CHECK (ends_on > starts_on)
);

CREATE TABLE IF NOT EXISTS core.teams (
    team_id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_name      text NOT NULL,
    normalized_name     text NOT NULL,
    country_code        char(2),
    stadium             text,
    active              boolean NOT NULL DEFAULT true,
    UNIQUE (normalized_name, country_code)
);

CREATE TABLE IF NOT EXISTS core.team_aliases (
    team_alias_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    team_id             bigint NOT NULL REFERENCES core.teams(team_id),
    source_name         text NOT NULL,
    competition_code    text NOT NULL,
    source_alias        text NOT NULL,
    normalized_alias    text NOT NULL,
    verified            boolean NOT NULL DEFAULT false,
    verification_method text,
    evidence            jsonb NOT NULL DEFAULT '{}'::jsonb,
    verified_at_utc     timestamptz,
    UNIQUE (source_name, competition_code, normalized_alias)
);

CREATE INDEX IF NOT EXISTS idx_team_aliases_team
    ON core.team_aliases (team_id, source_name);
CREATE INDEX IF NOT EXISTS idx_team_aliases_source_normalized
    ON core.team_aliases (source_name, normalized_alias);

CREATE TABLE IF NOT EXISTS core.players (
    player_id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_name      text NOT NULL,
    normalized_name     text NOT NULL,
    date_of_birth       date,
    nationality_code    char(2),
    preferred_position  text,
    height_cm           smallint CHECK (height_cm BETWEEN 130 AND 230),
    preferred_foot      text CHECK (preferred_foot IN ('left','right','both','unknown')),
    active              boolean NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS idx_players_normalized_name
    ON core.players (normalized_name);

CREATE TABLE IF NOT EXISTS core.source_entity_mappings (
    mapping_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_type         text NOT NULL CHECK (entity_type IN ('competition','season','team','player','match')),
    canonical_id        bigint NOT NULL,
    source_name         text NOT NULL,
    source_entity_id    text NOT NULL,
    confidence          numeric(5,4) NOT NULL DEFAULT 1 CHECK (confidence BETWEEN 0 AND 1),
    verified_at_utc     timestamptz,
    UNIQUE (entity_type, source_name, source_entity_id)
);

CREATE TABLE IF NOT EXISTS core.team_season_memberships (
    team_id             bigint NOT NULL REFERENCES core.teams(team_id),
    season_id           bigint NOT NULL REFERENCES core.seasons(season_id),
    joined_on           date,
    left_on             date,
    PRIMARY KEY (team_id, season_id)
);

CREATE TABLE IF NOT EXISTS core.player_team_periods (
    player_team_period_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    player_id           bigint NOT NULL REFERENCES core.players(player_id),
    team_id             bigint NOT NULL REFERENCES core.teams(team_id),
    starts_on           date NOT NULL,
    ends_on             date,
    shirt_number        smallint,
    CHECK (ends_on IS NULL OR ends_on >= starts_on)
);

CREATE INDEX IF NOT EXISTS idx_player_team_period
    ON core.player_team_periods (player_id, starts_on DESC);

CREATE TABLE IF NOT EXISTS core.matches (
    match_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    season_id           bigint NOT NULL REFERENCES core.seasons(season_id),
    home_team_id        bigint NOT NULL REFERENCES core.teams(team_id),
    away_team_id        bigint NOT NULL REFERENCES core.teams(team_id),
    kickoff_utc         timestamptz NOT NULL,
    venue               text,
    matchday            smallint,
    status              text NOT NULL CHECK (status IN ('scheduled','postponed','live','finished','cancelled')),
    home_goals          smallint CHECK (home_goals >= 0),
    away_goals          smallint CHECK (away_goals >= 0),
    last_source_update_utc timestamptz,
    created_at_utc      timestamptz NOT NULL DEFAULT now(),
    updated_at_utc      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (season_id, home_team_id, away_team_id, kickoff_utc),
    CHECK (home_team_id <> away_team_id)
);

CREATE INDEX IF NOT EXISTS idx_matches_season_kickoff
    ON core.matches (season_id, kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_matches_home_kickoff
    ON core.matches (home_team_id, kickoff_utc DESC);
CREATE INDEX IF NOT EXISTS idx_matches_away_kickoff
    ON core.matches (away_team_id, kickoff_utc DESC);

CREATE TABLE IF NOT EXISTS core.lineups (
    lineup_id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id            bigint NOT NULL REFERENCES core.matches(match_id),
    team_id             bigint NOT NULL REFERENCES core.teams(team_id),
    player_id           bigint NOT NULL REFERENCES core.players(player_id),
    lineup_type         text NOT NULL CHECK (lineup_type IN ('predicted','confirmed')),
    role                text,
    formation_slot      text,
    is_starter          boolean NOT NULL,
    expected_minutes    numeric(5,2) CHECK (expected_minutes BETWEEN 0 AND 130),
    confidence          numeric(5,4) CHECK (confidence BETWEEN 0 AND 1),
    captured_at_utc     timestamptz NOT NULL,
    UNIQUE (match_id, team_id, player_id, lineup_type, captured_at_utc)
);

CREATE INDEX IF NOT EXISTS idx_lineups_match_capture
    ON core.lineups (match_id, lineup_type, captured_at_utc DESC);

CREATE TABLE IF NOT EXISTS core.match_events (
    event_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id            bigint NOT NULL REFERENCES core.matches(match_id),
    source_name         text NOT NULL,
    source_event_id     text NOT NULL,
    team_id             bigint REFERENCES core.teams(team_id),
    player_id           bigint REFERENCES core.players(player_id),
    related_player_id   bigint REFERENCES core.players(player_id),
    event_type          text NOT NULL,
    event_time_utc      timestamptz,
    minute              smallint CHECK (minute BETWEEN 0 AND 140),
    second              smallint CHECK (second BETWEEN 0 AND 59),
    period              smallint,
    x                   numeric(6,3),
    y                   numeric(6,3),
    qualifiers          jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (source_name, source_event_id)
);

CREATE INDEX IF NOT EXISTS idx_events_match_clock
    ON core.match_events (match_id, period, minute, second);
CREATE INDEX IF NOT EXISTS idx_events_player
    ON core.match_events (player_id, event_type);

CREATE TABLE IF NOT EXISTS core.player_match_stats (
    player_match_stat_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id            bigint NOT NULL REFERENCES core.matches(match_id),
    player_id           bigint NOT NULL REFERENCES core.players(player_id),
    team_id             bigint NOT NULL REFERENCES core.teams(team_id),
    position_bucket     text,
    minutes_played      smallint NOT NULL DEFAULT 0 CHECK (minutes_played BETWEEN 0 AND 130),
    started             boolean NOT NULL DEFAULT false,
    goals               smallint NOT NULL DEFAULT 0,
    assists             smallint NOT NULL DEFAULT 0,
    xg                  numeric(8,5),
    xa                  numeric(8,5),
    shots               smallint,
    shots_on_target     smallint,
    key_passes          smallint,
    progressive_passes  smallint,
    tackles             smallint,
    interceptions       smallint,
    duels_won           smallint,
    saves               smallint,
    raw_metrics         jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (match_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_player_stats_player_match
    ON core.player_match_stats (player_id, match_id);

CREATE TABLE IF NOT EXISTS core.injuries (
    injury_id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    player_id           bigint NOT NULL REFERENCES core.players(player_id),
    team_id             bigint NOT NULL REFERENCES core.teams(team_id),
    injury_type         text,
    status              text NOT NULL CHECK (status IN ('doubtful','out','suspended','available','unknown')),
    starts_on           date NOT NULL,
    expected_return_on  date,
    source_name         text NOT NULL,
    captured_at_utc     timestamptz NOT NULL,
    source_payload      jsonb,
    CHECK (expected_return_on IS NULL OR expected_return_on >= starts_on)
);

CREATE INDEX IF NOT EXISTS idx_injuries_team_status
    ON core.injuries (team_id, status, captured_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_injuries_player_status
    ON core.injuries (player_id, status, captured_at_utc DESC);

CREATE TABLE IF NOT EXISTS core.odds_snapshots (
    odds_snapshot_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id            bigint NOT NULL REFERENCES core.matches(match_id),
    source_name         text NOT NULL,
    bookmaker           text NOT NULL,
    market_type         text NOT NULL,
    selection_key       text NOT NULL,
    line_value          numeric(9,4),
    odds_decimal        numeric(10,4) NOT NULL CHECK (odds_decimal > 1),
    captured_at_utc     timestamptz NOT NULL,
    snapshot_type       text CHECK (snapshot_type IN ('opening','live','preclose','closing','historical')),
    liquidity           numeric(18,4),
    raw_market          jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (match_id, source_name, bookmaker, market_type, selection_key, captured_at_utc)
);

CREATE INDEX IF NOT EXISTS idx_odds_match_market_time
    ON core.odds_snapshots (match_id, market_type, captured_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_odds_bookmaker_time
    ON core.odds_snapshots (bookmaker, captured_at_utc DESC);

-- Historical results and odds remain separate from the live match tables so
-- they can be replayed exactly as supplied by football-data.co.uk.
CREATE TABLE IF NOT EXISTS core.historical_matches (
    hist_match_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    competition_code    text NOT NULL,
    source_division     text NOT NULL,
    season_label        text NOT NULL,
    match_date          date NOT NULL,
    match_time_local    time,
    source_timezone     text NOT NULL,
    home_team           text NOT NULL,
    away_team           text NOT NULL,
    home_team_norm      text NOT NULL,
    away_team_norm      text NOT NULL,
    home_goals          smallint NOT NULL CHECK (home_goals >= 0),
    away_goals          smallint NOT NULL CHECK (away_goals >= 0),
    full_time_result    char(1) NOT NULL CHECK (full_time_result IN ('H','D','A')),
    result_status       text NOT NULL DEFAULT 'unverified'
                        CONSTRAINT chk_historical_result_status
                        CHECK (result_status IN ('unverified','verified_normal','awarded','abandoned')),
    result_verification_source text,
    result_verification_report_sha256 char(64),
    result_verified_at_utc timestamptz,
    ht_home_goals       smallint CHECK (ht_home_goals >= 0),
    ht_away_goals       smallint CHECK (ht_away_goals >= 0),
    half_time_result    char(1) CHECK (half_time_result IN ('H','D','A')),
    team_id_home        bigint REFERENCES core.teams(team_id),
    team_id_away        bigint REFERENCES core.teams(team_id),
    source_file_id      bigint NOT NULL REFERENCES raw.source_files(source_file_id),
    source_row_number   integer NOT NULL CHECK (source_row_number >= 2),
    source_row_hash     char(64) NOT NULL,
    match_stats         jsonb NOT NULL DEFAULT '{}'::jsonb,
    quality_flags       jsonb NOT NULL DEFAULT '[]'::jsonb,
    raw_row             jsonb NOT NULL,
    imported_at_utc     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_division, season_label, match_date, home_team_norm, away_team_norm),
    CHECK (home_team_norm <> away_team_norm),
    CHECK (
        (home_goals > away_goals AND full_time_result = 'H') OR
        (home_goals = away_goals AND full_time_result = 'D') OR
        (home_goals < away_goals AND full_time_result = 'A')
    )
);

-- Keep the development bootstrap idempotent for databases created before the
-- historical verification fields existed. Production deployments should move
-- these statements into a versioned migration.
ALTER TABLE core.historical_matches
    ADD COLUMN IF NOT EXISTS result_status text NOT NULL DEFAULT 'unverified';
ALTER TABLE core.historical_matches
    ADD COLUMN IF NOT EXISTS result_verification_source text;
ALTER TABLE core.historical_matches
    ADD COLUMN IF NOT EXISTS result_verification_report_sha256 char(64);
ALTER TABLE core.historical_matches
    ADD COLUMN IF NOT EXISTS result_verified_at_utc timestamptz;
ALTER TABLE core.historical_matches
    ADD COLUMN IF NOT EXISTS quality_flags jsonb NOT NULL DEFAULT '[]'::jsonb;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_historical_result_status'
          AND conrelid = 'core.historical_matches'::regclass
    ) THEN
        ALTER TABLE core.historical_matches
            ADD CONSTRAINT chk_historical_result_status
            CHECK (result_status IN ('unverified','verified_normal','awarded','abandoned'));
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_historical_matches_comp_date
    ON core.historical_matches (competition_code, match_date);
CREATE INDEX IF NOT EXISTS idx_historical_matches_home_date
    ON core.historical_matches (home_team_norm, match_date DESC);
CREATE INDEX IF NOT EXISTS idx_historical_matches_away_date
    ON core.historical_matches (away_team_norm, match_date DESC);

CREATE TABLE IF NOT EXISTS core.historical_odds (
    hist_odds_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    hist_match_id       bigint NOT NULL REFERENCES core.historical_matches(hist_match_id) ON DELETE CASCADE,
    bookmaker           text NOT NULL,
    market_type         text NOT NULL CHECK (market_type IN ('1X2','OU25','AH')),
    is_closing          boolean NOT NULL,
    price_timing        text NOT NULL DEFAULT 'unknown',
    point_in_time_safe  boolean NOT NULL DEFAULT false,
    line_value          numeric(9,4),
    odd_home            numeric(10,4) CHECK (odd_home IS NULL OR odd_home > 1),
    odd_draw            numeric(10,4) CHECK (odd_draw IS NULL OR odd_draw > 1),
    odd_away            numeric(10,4) CHECK (odd_away IS NULL OR odd_away > 1),
    odd_over            numeric(10,4) CHECK (odd_over IS NULL OR odd_over > 1),
    odd_under           numeric(10,4) CHECK (odd_under IS NULL OR odd_under > 1),
    source_columns      jsonb NOT NULL,
    quality_flags       jsonb NOT NULL DEFAULT '[]'::jsonb,
    imported_at_utc     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (hist_match_id, bookmaker, market_type, is_closing),
    CONSTRAINT chk_historical_odds_price_timing
        CHECK (price_timing IN ('opening','closing','preclosing_legacy','unknown')),
    CONSTRAINT chk_historical_odds_closing_timing
        CHECK (
            (is_closing AND price_timing = 'closing') OR
            (NOT is_closing AND price_timing IN ('opening','preclosing_legacy','unknown'))
        ),
    CHECK (
        (market_type = '1X2' AND odd_home IS NOT NULL AND odd_draw IS NOT NULL AND odd_away IS NOT NULL) OR
        (market_type = 'OU25' AND odd_over IS NOT NULL AND odd_under IS NOT NULL) OR
        (market_type = 'AH' AND line_value IS NOT NULL AND odd_home IS NOT NULL AND odd_away IS NOT NULL)
    )
);

ALTER TABLE core.historical_odds
    ADD COLUMN IF NOT EXISTS price_timing text NOT NULL DEFAULT 'unknown';
ALTER TABLE core.historical_odds
    ADD COLUMN IF NOT EXISTS point_in_time_safe boolean NOT NULL DEFAULT false;

UPDATE core.historical_odds
SET price_timing = 'closing'
WHERE is_closing AND price_timing = 'unknown';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_historical_odds_price_timing'
          AND conrelid = 'core.historical_odds'::regclass
    ) THEN
        ALTER TABLE core.historical_odds
            ADD CONSTRAINT chk_historical_odds_price_timing
            CHECK (price_timing IN ('opening','closing','preclosing_legacy','unknown'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_historical_odds_closing_timing'
          AND conrelid = 'core.historical_odds'::regclass
    ) THEN
        ALTER TABLE core.historical_odds
            ADD CONSTRAINT chk_historical_odds_closing_timing
            CHECK (
                (is_closing AND price_timing = 'closing') OR
                (NOT is_closing AND price_timing IN ('opening','preclosing_legacy','unknown'))
            );
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_historical_odds_match_market
    ON core.historical_odds (hist_match_id, market_type, is_closing);

CREATE TABLE IF NOT EXISTS audit.historical_validation_reports (
    validation_report_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    report_sha256      char(64) NOT NULL UNIQUE,
    report_path        text NOT NULL,
    primary_source     text NOT NULL,
    verification_sources jsonb NOT NULL,
    season_label       text NOT NULL,
    matches_checked    integer NOT NULL CHECK (matches_checked >= 0),
    status             text NOT NULL CHECK (status IN ('pass','pass_with_warnings','fail')),
    summary            jsonb NOT NULL,
    warnings           jsonb NOT NULL DEFAULT '[]'::jsonb,
    generated_at_utc   timestamptz,
    applied_at_utc     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS analytics.player_ratings (
    player_rating_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id            bigint NOT NULL REFERENCES core.matches(match_id),
    player_id           bigint NOT NULL REFERENCES core.players(player_id),
    rating_version      text NOT NULL,
    position_bucket     text NOT NULL,
    role_score          numeric(7,3) NOT NULL,
    form_5              numeric(7,3),
    form_10             numeric(7,3),
    season_score        numeric(7,3),
    stability_score     numeric(7,3),
    volatility_score    numeric(7,3),
    confidence          numeric(5,4) CHECK (confidence BETWEEN 0 AND 1),
    factors             jsonb NOT NULL,
    created_at_utc      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (match_id, player_id, rating_version)
);

CREATE TABLE IF NOT EXISTS analytics.team_strength_snapshots (
    team_strength_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    team_id             bigint NOT NULL REFERENCES core.teams(team_id),
    as_of_utc           timestamptz NOT NULL,
    rating_version      text NOT NULL,
    attack_score        numeric(7,3) NOT NULL,
    defense_score       numeric(7,3) NOT NULL,
    midfield_score      numeric(7,3) NOT NULL,
    goalkeeping_score   numeric(7,3) NOT NULL,
    overall_score       numeric(7,3) NOT NULL,
    form_strength       numeric(7,3),
    momentum            numeric(7,3),
    injury_adjustment   numeric(7,3),
    fatigue_adjustment  numeric(7,3),
    lineup_confidence   numeric(5,4),
    UNIQUE (team_id, as_of_utc, rating_version)
);

CREATE INDEX IF NOT EXISTS idx_team_strength_latest
    ON analytics.team_strength_snapshots (team_id, as_of_utc DESC);

CREATE TABLE IF NOT EXISTS analytics.feature_snapshots (
    feature_snapshot_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id            bigint NOT NULL REFERENCES core.matches(match_id),
    feature_version     text NOT NULL,
    as_of_utc           timestamptz NOT NULL,
    features            jsonb NOT NULL,
    missingness         jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_fingerprint  text NOT NULL,
    created_at_utc      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (match_id, feature_version, as_of_utc)
);

CREATE TABLE IF NOT EXISTS analytics.model_versions (
    model_version_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_name          text NOT NULL,
    version             text NOT NULL,
    target_market       text NOT NULL,
    training_cutoff_utc timestamptz NOT NULL,
    feature_version     text NOT NULL,
    calibration_method  text,
    artifact_uri        text NOT NULL,
    metrics             jsonb NOT NULL,
    status              text NOT NULL CHECK (status IN ('candidate','shadow','active','retired')),
    created_at_utc      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (model_name, version, target_market)
);

CREATE TABLE IF NOT EXISTS analytics.model_predictions (
    prediction_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id            bigint NOT NULL REFERENCES core.matches(match_id),
    model_version_id    bigint NOT NULL REFERENCES analytics.model_versions(model_version_id),
    feature_snapshot_id bigint NOT NULL REFERENCES analytics.feature_snapshots(feature_snapshot_id),
    predicted_at_utc    timestamptz NOT NULL,
    probability_home    numeric(7,6) NOT NULL,
    probability_draw    numeric(7,6) NOT NULL,
    probability_away    numeric(7,6) NOT NULL,
    probability_over_25 numeric(7,6),
    probability_btts    numeric(7,6),
    score_distribution  jsonb,
    calibration_status  text NOT NULL,
    UNIQUE (match_id, model_version_id, predicted_at_utc),
    CHECK (probability_home BETWEEN 0 AND 1),
    CHECK (probability_draw BETWEEN 0 AND 1),
    CHECK (probability_away BETWEEN 0 AND 1),
    CHECK (abs(probability_home + probability_draw + probability_away - 1) < 0.0001)
);

CREATE INDEX IF NOT EXISTS idx_predictions_match_time
    ON analytics.model_predictions (match_id, predicted_at_utc DESC);

CREATE TABLE IF NOT EXISTS analytics.backtest_runs (
    backtest_run_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_version_id    bigint NOT NULL REFERENCES analytics.model_versions(model_version_id),
    name                text NOT NULL,
    train_start_on      date NOT NULL,
    train_end_on        date NOT NULL,
    test_start_on       date NOT NULL,
    test_end_on         date NOT NULL,
    configuration       jsonb NOT NULL,
    metrics             jsonb NOT NULL,
    created_at_utc      timestamptz NOT NULL DEFAULT now(),
    CHECK (train_start_on <= train_end_on),
    CHECK (test_start_on <= test_end_on),
    CHECK (train_end_on < test_start_on)
);

-- Historical research runs are kept separate from live-serving predictions.
-- Every prediction points directly to the immutable, hash-pinned historical row
-- that was evaluated, and records a cutoff strictly before the match date.
CREATE TABLE IF NOT EXISTS analytics.historical_backtest_runs (
    historical_backtest_run_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_key               char(64) NOT NULL UNIQUE,
    model_name            text NOT NULL,
    model_version         text NOT NULL,
    train_start_on        date NOT NULL,
    train_end_on          date NOT NULL,
    test_start_on         date NOT NULL,
    test_end_on           date NOT NULL,
    source_fingerprint    char(64) NOT NULL,
    odds_fingerprint      char(64) NOT NULL,
    implementation_fingerprint char(64) NOT NULL,
    prediction_content_sha256 char(64) NOT NULL,
    configuration         jsonb NOT NULL,
    metrics               jsonb NOT NULL,
    status                text NOT NULL
                          CHECK (status IN ('running','completed','failed')),
    created_at_utc        timestamptz NOT NULL DEFAULT now(),
    completed_at_utc      timestamptz,
    CHECK (train_start_on <= train_end_on),
    CHECK (test_start_on <= test_end_on),
    CHECK (train_end_on < test_start_on)
);

ALTER TABLE analytics.historical_backtest_runs
    ADD COLUMN IF NOT EXISTS odds_fingerprint char(64);
ALTER TABLE analytics.historical_backtest_runs
    ADD COLUMN IF NOT EXISTS implementation_fingerprint char(64);
ALTER TABLE analytics.historical_backtest_runs
    ADD COLUMN IF NOT EXISTS prediction_content_sha256 char(64);

CREATE TABLE IF NOT EXISTS analytics.historical_model_predictions (
    historical_prediction_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    historical_backtest_run_id bigint NOT NULL
        REFERENCES analytics.historical_backtest_runs(historical_backtest_run_id)
        ON DELETE CASCADE,
    hist_match_id          bigint NOT NULL
        REFERENCES core.historical_matches(hist_match_id)
        ON DELETE CASCADE,
    prediction_for_on      date NOT NULL,
    training_cutoff_on     date NOT NULL,
    source_max_match_date  date NOT NULL,
    probability_home      numeric(9,8) NOT NULL,
    probability_draw      numeric(9,8) NOT NULL,
    probability_away      numeric(9,8) NOT NULL,
    expected_home_goals   numeric(9,6) NOT NULL,
    expected_away_goals   numeric(9,6) NOT NULL,
    market_probability_home numeric(9,8),
    market_probability_draw numeric(9,8),
    market_probability_away numeric(9,8),
    actual_result         char(1) NOT NULL CHECK (actual_result IN ('H','D','A')),
    created_at_utc        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (historical_backtest_run_id, hist_match_id),
    CHECK (source_max_match_date <= training_cutoff_on),
    CHECK (training_cutoff_on < prediction_for_on),
    CHECK (probability_home BETWEEN 0 AND 1),
    CHECK (probability_draw BETWEEN 0 AND 1),
    CHECK (probability_away BETWEEN 0 AND 1),
    CHECK (abs(probability_home + probability_draw + probability_away - 1) < 0.000001),
    CHECK (
        (market_probability_home IS NULL
         AND market_probability_draw IS NULL
         AND market_probability_away IS NULL)
        OR
        (market_probability_home BETWEEN 0 AND 1
         AND market_probability_draw BETWEEN 0 AND 1
         AND market_probability_away BETWEEN 0 AND 1
         AND abs(
             market_probability_home
             + market_probability_draw
             + market_probability_away
             - 1
         ) < 0.000001)
    )
);

ALTER TABLE analytics.historical_model_predictions
    ADD COLUMN IF NOT EXISTS prediction_for_on date;
ALTER TABLE analytics.historical_model_predictions
    ADD COLUMN IF NOT EXISTS source_max_match_date date;

UPDATE analytics.historical_model_predictions p
SET prediction_for_on = hm.match_date,
    source_max_match_date = (
        SELECT max(prior.match_date)
        FROM core.historical_matches prior
        WHERE prior.competition_code = hm.competition_code
          AND prior.match_date < hm.match_date
          AND prior.result_status NOT IN ('awarded', 'abandoned')
    )
FROM core.historical_matches hm
WHERE hm.hist_match_id = p.hist_match_id
  AND (p.prediction_for_on IS NULL OR p.source_max_match_date IS NULL);

ALTER TABLE analytics.historical_model_predictions
    ALTER COLUMN prediction_for_on SET NOT NULL;
ALTER TABLE analytics.historical_model_predictions
    ALTER COLUMN source_max_match_date SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_historical_prediction_cutoff'
          AND conrelid = 'analytics.historical_model_predictions'::regclass
    ) THEN
        ALTER TABLE analytics.historical_model_predictions
            ADD CONSTRAINT chk_historical_prediction_cutoff
            CHECK (
                source_max_match_date <= training_cutoff_on
                AND training_cutoff_on < prediction_for_on
            );
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_historical_predictions_run_match
    ON analytics.historical_model_predictions
    (historical_backtest_run_id, hist_match_id);

CREATE TABLE IF NOT EXISTS audit.simulated_bets (
    simulated_bet_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prediction_id       bigint NOT NULL REFERENCES analytics.model_predictions(prediction_id),
    odds_snapshot_id    bigint NOT NULL REFERENCES core.odds_snapshots(odds_snapshot_id),
    backtest_run_id     bigint REFERENCES analytics.backtest_runs(backtest_run_id),
    market_type         text NOT NULL,
    selection_key       text NOT NULL,
    model_probability   numeric(7,6) NOT NULL,
    market_probability  numeric(7,6) NOT NULL,
    edge                 numeric(8,6) NOT NULL,
    expected_value      numeric(10,6) NOT NULL,
    stake_units         numeric(10,4) NOT NULL CHECK (stake_units > 0),
    status              text NOT NULL CHECK (status IN ('observing','placed','won','lost','void')),
    pnl_units           numeric(12,4),
    closing_odds        numeric(10,4),
    clv                 numeric(10,6),
    created_at_utc      timestamptz NOT NULL DEFAULT now(),
    settled_at_utc      timestamptz
);

CREATE INDEX IF NOT EXISTS idx_simulated_bets_status_time
    ON audit.simulated_bets (status, created_at_utc DESC);

CREATE TABLE IF NOT EXISTS audit.data_quality_checks (
    dq_check_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id            bigint REFERENCES raw.ingestion_batches(batch_id),
    check_name          text NOT NULL,
    entity_type         text NOT NULL,
    scope_key           text,
    metric_name         text NOT NULL,
    metric_value        numeric(18,6),
    threshold_value     numeric(18,6),
    status              text NOT NULL CHECK (status IN ('pass','warn','fail')),
    details             jsonb NOT NULL DEFAULT '{}'::jsonb,
    checked_at_utc      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_dq_status_time
    ON audit.data_quality_checks (status, checked_at_utc DESC);

CREATE TABLE IF NOT EXISTS audit.explanation_records (
    explanation_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prediction_id       bigint NOT NULL REFERENCES analytics.model_predictions(prediction_id),
    input_fingerprint   text NOT NULL,
    model_name          text NOT NULL,
    model_version       text NOT NULL,
    structured_output   jsonb NOT NULL,
    human_review_required boolean NOT NULL,
    created_at_utc      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit.user_actions (
    user_action_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id            text NOT NULL,
    action_type         text NOT NULL,
    entity_type         text NOT NULL,
    entity_id           text NOT NULL,
    request_id          text,
    before_state        jsonb,
    after_state         jsonb,
    created_at_utc      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_user_actions_entity
    ON audit.user_actions (entity_type, entity_id, created_at_utc DESC);

-- ============================================================
-- 竞彩官方赔率快照（跨市场价差研究的时间戳安全数据源）
-- 每条快照带 captured_at_utc，采集时刻即证明时刻，point_in_time_safe=true
-- ============================================================

CREATE SCHEMA IF NOT EXISTS jingcai;

CREATE TABLE IF NOT EXISTS jingcai.jc_matches (
    jc_match_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    official_match_id   text NOT NULL,          -- sporttery matchId
    match_num_str       text,                   -- 如 周五001
    league_code         text,
    league_name         text,
    home_team           text NOT NULL,
    away_team           text NOT NULL,
    match_date_local    date,                   -- 北京时间开赛日期
    match_time_local    text,                   -- 官方开赛时间原文
    sell_end_local      text,                   -- 官方停售时间原文
    first_seen_at_utc   timestamptz NOT NULL DEFAULT now(),
    last_seen_at_utc    timestamptz NOT NULL DEFAULT now(),
    raw_payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (official_match_id)
);

CREATE TABLE IF NOT EXISTS jingcai.jc_odds_snapshots (
    jc_snapshot_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    jc_match_id         bigint NOT NULL REFERENCES jingcai.jc_matches(jc_match_id),
    pool_code           text NOT NULL CHECK (pool_code IN ('had','hhad')),
    goal_line           text,                   -- hhad 让球数原文，had 为 NULL
    odd_home            numeric(10,4),
    odd_draw            numeric(10,4),
    odd_away            numeric(10,4),
    official_update_raw text,                   -- 官方 updateDate+updateTime 原文
    content_hash        text NOT NULL,          -- 赔率内容哈希，用于变更去重
    captured_at_utc     timestamptz NOT NULL DEFAULT now(),
    last_observed_at_utc timestamptz NOT NULL DEFAULT now(),
    source              text NOT NULL DEFAULT 'webapi.sporttery.cn',
    point_in_time_safe  boolean NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS idx_jc_snapshots_match_pool_time
    ON jingcai.jc_odds_snapshots (jc_match_id, pool_code, captured_at_utc DESC);

ALTER TABLE jingcai.jc_odds_snapshots
    ADD COLUMN IF NOT EXISTS last_observed_at_utc timestamptz;
UPDATE jingcai.jc_odds_snapshots
SET last_observed_at_utc = captured_at_utc
WHERE last_observed_at_utc IS NULL;
ALTER TABLE jingcai.jc_odds_snapshots
    ALTER COLUMN last_observed_at_utc SET DEFAULT now(),
    ALTER COLUMN last_observed_at_utc SET NOT NULL;

-- 国际市场共识赔率快照（价差对照的另一侧；provider 可插拔）
CREATE TABLE IF NOT EXISTS jingcai.consensus_odds_snapshots (
    consensus_snapshot_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    jc_match_id         bigint REFERENCES jingcai.jc_matches(jc_match_id),
    provider            text NOT NULL,          -- 如 the-odds-api
    bookmaker           text NOT NULL,          -- 如 pinnacle
    provider_event_id   text,
    home_team           text,
    away_team           text,
    commence_time_utc   timestamptz,
    market_type         text NOT NULL DEFAULT '1X2',
    odd_home            numeric(10,4),
    odd_draw            numeric(10,4),
    odd_away            numeric(10,4),
    content_hash        text NOT NULL,
    captured_at_utc     timestamptz NOT NULL DEFAULT now(),
    last_observed_at_utc timestamptz NOT NULL DEFAULT now(),
    point_in_time_safe  boolean NOT NULL DEFAULT true,
    raw_payload         jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_consensus_snapshots_match_time
    ON jingcai.consensus_odds_snapshots (jc_match_id, bookmaker, captured_at_utc DESC);

-- Retain real unchanged-price observations; first/last alone lose intermediate evidence.
ALTER TABLE jingcai.jc_odds_snapshots
    ADD COLUMN IF NOT EXISTS observation_times_utc timestamptz[] NOT NULL DEFAULT '{}';
ALTER TABLE jingcai.consensus_odds_snapshots
    ADD COLUMN IF NOT EXISTS observation_times_utc timestamptz[] NOT NULL DEFAULT '{}';

ALTER TABLE jingcai.consensus_odds_snapshots
    ADD COLUMN IF NOT EXISTS sport_key text;
ALTER TABLE jingcai.consensus_odds_snapshots
    ADD COLUMN IF NOT EXISTS last_observed_at_utc timestamptz;
UPDATE jingcai.consensus_odds_snapshots
SET last_observed_at_utc = captured_at_utc
WHERE last_observed_at_utc IS NULL;
ALTER TABLE jingcai.consensus_odds_snapshots
    ALTER COLUMN last_observed_at_utc SET DEFAULT now(),
    ALTER COLUMN last_observed_at_utc SET NOT NULL;

-- 竞彩历史赛果与最终固定赔率(开奖公告接口回填; 无逐条时间戳,
-- 最终赔率≈停售价, point_in_time_safe=false, 仅用于价差回测)
CREATE TABLE IF NOT EXISTS jingcai.jc_result_history (
    jc_result_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    official_match_id   bigint NOT NULL UNIQUE,
    match_date          date NOT NULL,
    match_num_str       text,
    league_id           bigint,
    league_abbr         text,
    league_name         text,
    home_team           text NOT NULL,
    away_team           text NOT NULL,
    home_team_full      text,
    away_team_full      text,
    goal_line           text,
    odd_home            numeric(10,4),
    odd_draw            numeric(10,4),
    odd_away            numeric(10,4),
    win_flag            text,               -- H/D/A(胜平负彩果)
    ht_score            text,
    ft_score            text,
    pool_status         text,
    point_in_time_safe  boolean NOT NULL DEFAULT false,
    raw_payload         jsonb NOT NULL DEFAULT '{}'::jsonb,
    fetched_at_utc      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_jc_result_history_date_league
    ON jingcai.jc_result_history (match_date, league_abbr);

COMMIT;

-- 生产环境建议：
-- 1. core.match_events 与 core.odds_snapshots 按赛季或月份做 declarative partitioning。
-- 2. 大体量时序表可迁移为 Timescale hypertable，并为聚合视图设置 retention policy。
-- 3. raw payload 与敏感账户数据使用不同数据库角色；真实账户数据不进入本 Schema。
