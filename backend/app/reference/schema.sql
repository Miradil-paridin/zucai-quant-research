CREATE TABLE IF NOT EXISTS analytics.context_batches (
  dataset_id text NOT NULL,
  version text NOT NULL,
  imported_at_utc timestamptz NOT NULL DEFAULT now(),
  content_sha256 char(64) NOT NULL,
  payload jsonb NOT NULL,
  PRIMARY KEY(dataset_id,version)
);
CREATE TABLE IF NOT EXISTS analytics.match_context_dossiers (
  dataset_id text NOT NULL,
  version text NOT NULL,
  match_id bigint NOT NULL REFERENCES core.matches(match_id),
  payload jsonb NOT NULL,
  PRIMARY KEY(dataset_id,version,match_id),
  FOREIGN KEY(dataset_id,version) REFERENCES analytics.context_batches(dataset_id,version),
  CHECK(payload->>'provenance'='historical_backfill'),
  CHECK(payload->'point_in_time_verified'='false'::jsonb),
  CHECK(payload->'eligible_for_prediction_backtest'='false'::jsonb)
);
CREATE OR REPLACE FUNCTION analytics.protect_context_research() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Historical research is immutable; import a new version'; END $$;
DROP TRIGGER IF EXISTS immutable_context_batch ON analytics.context_batches;
CREATE TRIGGER immutable_context_batch BEFORE UPDATE OR DELETE ON analytics.context_batches
FOR EACH ROW EXECUTE FUNCTION analytics.protect_context_research();
DROP TRIGGER IF EXISTS immutable_context_dossier ON analytics.match_context_dossiers;
CREATE TRIGGER immutable_context_dossier BEFORE UPDATE OR DELETE ON analytics.match_context_dossiers
FOR EACH ROW EXECUTE FUNCTION analytics.protect_context_research();

CREATE TABLE IF NOT EXISTS analytics.reference_snapshots (
  reference_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  match_id bigint NOT NULL REFERENCES core.matches(match_id),
  kickoff_utc timestamptz NOT NULL,
  phase text NOT NULL CHECK (phase IN ('h24','h6','h1','manual')),
  version text NOT NULL,
  created_at_utc timestamptz NOT NULL DEFAULT now(),
  probability_home double precision NOT NULL CHECK(probability_home BETWEEN 0 AND 1),
  probability_draw double precision NOT NULL CHECK(probability_draw BETWEEN 0 AND 1),
  probability_away double precision NOT NULL CHECK(probability_away BETWEEN 0 AND 1),
  payload jsonb NOT NULL,
  content_sha256 char(64) NOT NULL,
  UNIQUE(match_id,kickoff_utc,phase,version),
  CHECK(created_at_utc < kickoff_utc),
  CHECK(abs(probability_home+probability_draw+probability_away-1)<0.000001)
);
CREATE OR REPLACE FUNCTION analytics.protect_reference_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Reference snapshots are immutable; create a new version instead'; END $$;
DROP TRIGGER IF EXISTS immutable_reference_snapshot ON analytics.reference_snapshots;
CREATE TRIGGER immutable_reference_snapshot BEFORE UPDATE OR DELETE ON analytics.reference_snapshots
FOR EACH ROW EXECUTE FUNCTION analytics.protect_reference_snapshot();
CREATE TABLE IF NOT EXISTS analytics.reference_results (
  reference_id bigint PRIMARY KEY REFERENCES analytics.reference_snapshots(reference_id),
  outcome text NOT NULL CHECK(outcome IN ('H','D','A','void')),
  home_goals integer,
  away_goals integer,
  source text NOT NULL,
  evidence jsonb NOT NULL,
  settled_at_utc timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS audit.reference_attempts (
  attempt_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  match_id bigint NOT NULL REFERENCES core.matches(match_id),
  kickoff_utc timestamptz NOT NULL,
  phase text NOT NULL,
  attempted_at_utc timestamptz NOT NULL DEFAULT now(),
  status text NOT NULL,
  details jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS reference_attempts_match ON audit.reference_attempts(match_id,attempted_at_utc DESC);
ALTER TABLE core.matches ADD COLUMN IF NOT EXISTS regulation_home_goals smallint;
ALTER TABLE core.matches ADD COLUMN IF NOT EXISTS regulation_away_goals smallint;
ALTER TABLE core.matches ADD COLUMN IF NOT EXISTS result_duration text;

CREATE TABLE IF NOT EXISTS analytics.prematch_research (
  research_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  match_id bigint NOT NULL REFERENCES core.matches(match_id),
  kickoff_utc timestamptz NOT NULL,
  version text NOT NULL,
  as_of_utc timestamptz NOT NULL,
  created_at_utc timestamptz NOT NULL DEFAULT now(),
  payload jsonb NOT NULL,
  content_sha256 char(64) NOT NULL,
  UNIQUE(match_id,kickoff_utc,version),
  CHECK(as_of_utc <= created_at_utc AND created_at_utc < kickoff_utc),
  CHECK(payload->>'kind'='qualitative_assessment'),
  CHECK(payload->'probabilities'='null'::jsonb)
);
DROP TRIGGER IF EXISTS immutable_prematch_research ON analytics.prematch_research;
CREATE TRIGGER immutable_prematch_research BEFORE UPDATE OR DELETE ON analytics.prematch_research
FOR EACH ROW EXECUTE FUNCTION analytics.protect_reference_snapshot();
