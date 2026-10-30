"""Controlled exploratory profiles and durable numerical executions."""
from alembic import op
revision = '0010_tool_runs'
down_revision = '0009_diagnostic_contexts'


def upgrade():
    op.get_bind().exec_driver_sql('''
CREATE TABLE tool_profiles (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL, asset_id uuid NOT NULL,
 tool_name text NOT NULL CHECK(tool_name='analyze_envelope'), tool_version text NOT NULL,
 configuration jsonb NOT NULL CHECK(jsonb_typeof(configuration)='object'),
 config_hash text NOT NULL CHECK(config_hash ~ '^[a-f0-9]{64}$'),
 qualification_status text NOT NULL DEFAULT 'unqualified' CHECK(qualification_status='unqualified'),
 created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,organization_id),
 FOREIGN KEY(asset_id,organization_id) REFERENCES assets(id,organization_id),
 FOREIGN KEY(created_by,organization_id) REFERENCES users(id,organization_id)
);
CREATE TABLE tool_runs (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL, measurement_revision_id uuid NOT NULL,
 confirmation_id uuid NOT NULL, context_id uuid, profile_id uuid, requested_by uuid NOT NULL,
 tool_name text NOT NULL CHECK(tool_name IN ('analyze_envelope','calculate_bearing_frequencies')),
 tool_version text NOT NULL, retry_of uuid,
 status text NOT NULL CHECK(status IN ('running','completed','failed','interrupted')),
 result_status text CHECK(result_status IN ('ok','unavailable','failed')),
 input_fingerprint text NOT NULL CHECK(input_fingerprint ~ '^[a-f0-9]{64}$'),
 input_snapshot jsonb NOT NULL CHECK(jsonb_typeof(input_snapshot)='object'),
 result_metadata jsonb, error_code text,
 started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 UNIQUE(id,organization_id),
 CHECK((status='running' AND finished_at IS NULL AND result_status IS NULL AND result_metadata IS NULL)
    OR (status='completed' AND finished_at IS NOT NULL AND result_status IS NOT NULL AND result_status IN ('ok','unavailable') AND result_metadata IS NOT NULL)
    OR (status='failed' AND finished_at IS NOT NULL AND result_status IS NOT NULL AND result_status='failed' AND error_code IS NOT NULL)
    OR (status='interrupted' AND finished_at IS NOT NULL AND result_status IS NULL AND error_code IS NOT NULL)),
 CHECK((tool_name='analyze_envelope' AND profile_id IS NOT NULL)
    OR (tool_name='calculate_bearing_frequencies' AND context_id IS NOT NULL AND profile_id IS NULL)),
 FOREIGN KEY(measurement_revision_id,organization_id) REFERENCES measurement_revisions(id,organization_id),
 FOREIGN KEY(confirmation_id,measurement_revision_id,organization_id) REFERENCES input_confirmations(id,revision_id,organization_id),
 FOREIGN KEY(context_id,organization_id) REFERENCES diagnostic_contexts(id,organization_id),
 FOREIGN KEY(profile_id,organization_id) REFERENCES tool_profiles(id,organization_id),
 FOREIGN KEY(requested_by,organization_id) REFERENCES users(id,organization_id),
 FOREIGN KEY(retry_of,organization_id) REFERENCES tool_runs(id,organization_id)
);
CREATE INDEX tool_run_input_idx ON tool_runs(organization_id,measurement_revision_id,started_at,id);
CREATE TABLE tool_artifacts (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL, tool_run_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('result','arrays','manifest')),
 storage_key text NOT NULL UNIQUE, sha256 text NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
 byte_size bigint NOT NULL CHECK(byte_size>0 AND byte_size<=67108864),
 format text NOT NULL CHECK(format IN ('json','npz')), state text NOT NULL CHECK(state='ready'),
 UNIQUE(tool_run_id,kind),
 FOREIGN KEY(tool_run_id,organization_id) REFERENCES tool_runs(id,organization_id)
);
CREATE TABLE tool_idempotency_records (
 organization_id uuid NOT NULL, actor_id uuid NOT NULL,
 key_hash text NOT NULL CHECK(key_hash ~ '^[a-f0-9]{64}$'),
 request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
 resource_id uuid NOT NULL, state text NOT NULL CHECK(state IN ('running','completed','failed','interrupted')),
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(organization_id,actor_id,key_hash),
 FOREIGN KEY(actor_id,organization_id) REFERENCES users(id,organization_id),
 FOREIGN KEY(resource_id,organization_id) REFERENCES tool_runs(id,organization_id)
);
CREATE TRIGGER tool_profile_immutable BEFORE UPDATE OR DELETE ON tool_profiles
 FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE FUNCTION protect_tool_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR OLD.status<>'running' THEN
  RAISE EXCEPTION 'Tool execution history is immutable';
 END IF;
 IF (NEW.id,NEW.organization_id,NEW.measurement_revision_id,NEW.confirmation_id,NEW.context_id,NEW.profile_id,
     NEW.requested_by,NEW.tool_name,NEW.tool_version,NEW.retry_of,NEW.input_fingerprint,NEW.input_snapshot,NEW.started_at)
  IS DISTINCT FROM
    (OLD.id,OLD.organization_id,OLD.measurement_revision_id,OLD.confirmation_id,OLD.context_id,OLD.profile_id,
     OLD.requested_by,OLD.tool_name,OLD.tool_version,OLD.retry_of,OLD.input_fingerprint,OLD.input_snapshot,OLD.started_at) THEN
  RAISE EXCEPTION 'Accepted tool snapshot is immutable';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tool_run_immutable BEFORE UPDATE OR DELETE ON tool_runs
 FOR EACH ROW EXECUTE FUNCTION protect_tool_run();
CREATE FUNCTION check_tool_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r measurement_revisions; c diagnostic_contexts; p tool_profiles;
BEGIN
 SELECT * INTO r FROM measurement_revisions WHERE id=NEW.measurement_revision_id AND organization_id=NEW.organization_id;
 IF NEW.context_id IS NOT NULL THEN
  SELECT * INTO c FROM diagnostic_contexts WHERE id=NEW.context_id AND organization_id=NEW.organization_id;
  IF c.measurement_revision_id IS DISTINCT FROM NEW.measurement_revision_id THEN
   RAISE EXCEPTION 'Tool context must belong to input revision';
  END IF;
 END IF;
 IF NEW.profile_id IS NOT NULL THEN
  SELECT * INTO p FROM tool_profiles WHERE id=NEW.profile_id AND organization_id=NEW.organization_id;
  IF p.asset_id IS DISTINCT FROM r.asset_id OR p.tool_name IS DISTINCT FROM NEW.tool_name THEN
   RAISE EXCEPTION 'Profile scope must match input asset and tool';
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tool_binding BEFORE INSERT ON tool_runs FOR EACH ROW EXECUTE FUNCTION check_tool_binding();
CREATE FUNCTION protect_tool_artifact() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE run_status text;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Tool artifacts are immutable'; END IF;
 SELECT status INTO run_status FROM tool_runs WHERE id=NEW.tool_run_id AND organization_id=NEW.organization_id FOR UPDATE;
 IF run_status IS DISTINCT FROM 'running' THEN RAISE EXCEPTION 'Tool is not running'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tool_artifact_immutable BEFORE INSERT OR UPDATE OR DELETE ON tool_artifacts
 FOR EACH ROW EXECUTE FUNCTION protect_tool_artifact();
CREATE FUNCTION check_tool_completion() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r tool_runs; kinds text[]; expected text[];
BEGIN
 IF TG_TABLE_NAME='tool_runs' THEN
  SELECT * INTO r FROM tool_runs WHERE id=NEW.id;
 ELSE
  SELECT * INTO r FROM tool_runs WHERE id=NEW.tool_run_id;
 END IF;
 SELECT array_agg(kind ORDER BY kind) INTO kinds FROM tool_artifacts WHERE tool_run_id=r.id;
 IF r.status='completed' THEN
  expected:=CASE WHEN r.tool_name='analyze_envelope' AND r.result_status='ok'
   THEN ARRAY['arrays','manifest','result'] ELSE ARRAY['manifest','result'] END;
  IF kinds IS DISTINCT FROM expected THEN RAISE EXCEPTION 'Tool artifact set is incomplete'; END IF;
  IF r.result_metadata->>'status' IS DISTINCT FROM r.result_status
   OR r.result_metadata->>'tool_name' IS DISTINCT FROM r.tool_name
   OR r.result_metadata->>'tool_version' IS DISTINCT FROM r.tool_version THEN
   RAISE EXCEPTION 'Tool outcome does not match run';
  END IF;
 ELSIF kinds IS NOT NULL THEN RAISE EXCEPTION 'Unfinished/failed tools cannot register artifacts';
 END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER tool_completion AFTER INSERT OR UPDATE ON tool_runs
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_tool_completion();
CREATE CONSTRAINT TRIGGER tool_artifact_completion AFTER INSERT ON tool_artifacts
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_tool_completion();
''')


def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
