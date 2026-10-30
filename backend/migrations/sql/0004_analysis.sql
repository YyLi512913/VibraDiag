CREATE TABLE analysis_runs (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL, measurement_revision_id uuid NOT NULL,
 confirmation_id uuid NOT NULL, requested_by uuid NOT NULL, retry_of uuid,
 status text NOT NULL CHECK(status IN ('running','completed','failed','interrupted')),
 algorithm_version text NOT NULL, config_hash text NOT NULL CHECK(config_hash ~ '^[a-f0-9]{64}$'),
 config_snapshot jsonb NOT NULL, started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 metrics jsonb, warnings jsonb NOT NULL DEFAULT '[]', error_code text,
 UNIQUE(id,organization_id),
 CHECK((status='running' AND finished_at IS NULL AND metrics IS NULL AND error_code IS NULL)
 OR (status='completed' AND finished_at IS NOT NULL AND metrics IS NOT NULL AND jsonb_typeof(metrics)='object' AND error_code IS NULL)
 OR (status IN ('failed','interrupted') AND finished_at IS NOT NULL AND error_code IS NOT NULL)),
 FOREIGN KEY(confirmation_id,measurement_revision_id,organization_id) REFERENCES input_confirmations(id,revision_id,organization_id),
 FOREIGN KEY(requested_by,organization_id) REFERENCES users(id,organization_id),
 FOREIGN KEY(retry_of,organization_id) REFERENCES analysis_runs(id,organization_id)
);
CREATE TABLE analysis_artifacts (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL, analysis_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('result','waveform_preview','amplitude_spectrum','manifest')),
 storage_key text NOT NULL UNIQUE, sha256 text NOT NULL CHECK(sha256 ~ '^[a-f0-9]{64}$'),
 byte_size bigint NOT NULL CHECK(byte_size>0), format text NOT NULL CHECK(format IN ('json','npz')),
 manifest jsonb NOT NULL, state text NOT NULL CHECK(state IN ('ready','corrupt','missing','archived')),
 UNIQUE(analysis_id,kind),
 FOREIGN KEY(analysis_id,organization_id) REFERENCES analysis_runs(id,organization_id)
);
CREATE TABLE idempotency_records (
 organization_id uuid NOT NULL, actor_id uuid NOT NULL, operation text NOT NULL,
 key_hash text NOT NULL CHECK(key_hash ~ '^[a-f0-9]{64}$'), request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
 resource_id uuid NOT NULL, state text NOT NULL CHECK(state IN ('running','completed','failed','interrupted')),
 response_summary jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(organization_id,actor_id,operation,key_hash),
 FOREIGN KEY(actor_id,organization_id) REFERENCES users(id,organization_id),
 FOREIGN KEY(resource_id,organization_id) REFERENCES analysis_runs(id,organization_id)
);
CREATE FUNCTION check_completed_artifacts() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE run_id uuid; run_status text;
BEGIN
 IF TG_TABLE_NAME='analysis_runs' THEN run_id:=NEW.id;
 ELSE run_id:=COALESCE(NEW.analysis_id,OLD.analysis_id); END IF;
 SELECT status INTO run_status FROM analysis_runs WHERE id=run_id;
 IF run_status='completed' AND
 (SELECT count(*) FROM analysis_artifacts WHERE analysis_id=run_id AND state='ready')<>4
 THEN RAISE EXCEPTION 'completed analysis requires four ready artifacts'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER completed_artifacts_run AFTER INSERT OR UPDATE ON analysis_runs
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_completed_artifacts();
CREATE CONSTRAINT TRIGGER completed_artifacts_files AFTER INSERT OR UPDATE OR DELETE ON analysis_artifacts
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_completed_artifacts();
CREATE FUNCTION reject_completed_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.status IN ('completed','failed','interrupted') THEN RAISE EXCEPTION 'terminal analysis is immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER analysis_terminal_immutable BEFORE UPDATE OR DELETE ON analysis_runs
 FOR EACH ROW EXECUTE FUNCTION reject_completed_mutation();
