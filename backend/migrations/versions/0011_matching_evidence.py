"""Matching lineage and immutable exploratory facts/evidence."""
from alembic import op
revision='0011_matching_evidence'
down_revision='0010_tool_runs'


def upgrade():
    op.get_bind().exec_driver_sql('''
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='tool_runs'::regclass
  AND contype='c' AND pg_get_constraintdef(oid) LIKE '%%tool_name%%' LOOP
  EXECUTE format('ALTER TABLE tool_runs DROP CONSTRAINT %%I',c.conname);
 END LOOP;
END $$;
ALTER TABLE tool_runs
 ADD COLUMN envelope_run_id uuid, ADD COLUMN frequency_run_id uuid, ADD COLUMN basic_analysis_id uuid,
 ADD CONSTRAINT tool_names CHECK(tool_name IN ('analyze_envelope','calculate_bearing_frequencies','match_frequency_evidence')),
 ADD CONSTRAINT tool_input_shape CHECK(
  (tool_name='analyze_envelope' AND profile_id IS NOT NULL AND envelope_run_id IS NULL AND frequency_run_id IS NULL AND basic_analysis_id IS NULL)
  OR (tool_name='calculate_bearing_frequencies' AND context_id IS NOT NULL AND profile_id IS NULL AND envelope_run_id IS NULL AND frequency_run_id IS NULL AND basic_analysis_id IS NULL)
  OR (tool_name='match_frequency_evidence' AND context_id IS NOT NULL AND profile_id IS NULL AND envelope_run_id IS NOT NULL AND frequency_run_id IS NOT NULL AND basic_analysis_id IS NOT NULL)),
 ADD FOREIGN KEY(envelope_run_id,organization_id) REFERENCES tool_runs(id,organization_id),
 ADD FOREIGN KEY(frequency_run_id,organization_id) REFERENCES tool_runs(id,organization_id),
 ADD FOREIGN KEY(basic_analysis_id,organization_id) REFERENCES analysis_runs(id,organization_id);
CREATE OR REPLACE FUNCTION protect_tool_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR OLD.status<>'running' THEN RAISE EXCEPTION 'Tool execution history is immutable'; END IF;
 IF (NEW.id,NEW.organization_id,NEW.measurement_revision_id,NEW.confirmation_id,NEW.context_id,NEW.profile_id,
     NEW.requested_by,NEW.tool_name,NEW.tool_version,NEW.retry_of,NEW.input_fingerprint,NEW.input_snapshot,NEW.started_at,
     NEW.envelope_run_id,NEW.frequency_run_id,NEW.basic_analysis_id)
  IS DISTINCT FROM
    (OLD.id,OLD.organization_id,OLD.measurement_revision_id,OLD.confirmation_id,OLD.context_id,OLD.profile_id,
     OLD.requested_by,OLD.tool_name,OLD.tool_version,OLD.retry_of,OLD.input_fingerprint,OLD.input_snapshot,OLD.started_at,
     OLD.envelope_run_id,OLD.frequency_run_id,OLD.basic_analysis_id) THEN
  RAISE EXCEPTION 'Accepted tool snapshot is immutable';
 END IF; RETURN NEW;
END $$;
CREATE FUNCTION check_matching_parents() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e tool_runs; f tool_runs; b analysis_runs;
BEGIN
 IF NEW.tool_name<>'match_frequency_evidence' THEN RETURN NEW; END IF;
 SELECT * INTO e FROM tool_runs WHERE id=NEW.envelope_run_id AND organization_id=NEW.organization_id;
 SELECT * INTO f FROM tool_runs WHERE id=NEW.frequency_run_id AND organization_id=NEW.organization_id;
 SELECT * INTO b FROM analysis_runs WHERE id=NEW.basic_analysis_id AND organization_id=NEW.organization_id;
 IF e.tool_name IS DISTINCT FROM 'analyze_envelope' OR f.tool_name IS DISTINCT FROM 'calculate_bearing_frequencies'
  OR e.status IS DISTINCT FROM 'completed' OR f.status IS DISTINCT FROM 'completed' OR b.status IS DISTINCT FROM 'completed'
  OR e.result_status IS DISTINCT FROM 'ok' OR f.result_status IS DISTINCT FROM 'ok'
  OR e.measurement_revision_id IS DISTINCT FROM NEW.measurement_revision_id OR f.measurement_revision_id IS DISTINCT FROM NEW.measurement_revision_id
  OR b.measurement_revision_id IS DISTINCT FROM NEW.measurement_revision_id OR e.context_id IS DISTINCT FROM NEW.context_id
  OR f.context_id IS DISTINCT FROM NEW.context_id THEN RAISE EXCEPTION 'Matching parents do not match input/context'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER matching_parent_binding BEFORE INSERT ON tool_runs FOR EACH ROW EXECUTE FUNCTION check_matching_parents();
CREATE TABLE analysis_facts (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL, tool_run_id uuid NOT NULL,
 measurement_revision_id uuid NOT NULL, context_id uuid NOT NULL, root_file_object_id uuid NOT NULL,
 input_sha256 text NOT NULL CHECK(input_sha256 ~ '^[a-f0-9]{64}$'),
 fact_key text NOT NULL, kind text NOT NULL CHECK(kind IN ('calculation_fact','observation_fact','matching_relation')),
 content jsonb NOT NULL CHECK(jsonb_typeof(content)='object'), parent_fact_ids uuid[] NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id,organization_id), UNIQUE(tool_run_id,fact_key),
 FOREIGN KEY(tool_run_id,organization_id) REFERENCES tool_runs(id,organization_id),
 FOREIGN KEY(measurement_revision_id,organization_id) REFERENCES measurement_revisions(id,organization_id),
 FOREIGN KEY(context_id,organization_id) REFERENCES diagnostic_contexts(id,organization_id),
 FOREIGN KEY(root_file_object_id,organization_id) REFERENCES file_objects(id,organization_id)
);
CREATE TABLE evidence_items (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL, tool_run_id uuid NOT NULL, fact_id uuid NOT NULL,
 purpose text NOT NULL CHECK(purpose='exploratory_observation'),
 polarity text NOT NULL CHECK(polarity IN ('observed_support','no_observed_support','ambiguous','not_evaluable')),
 content jsonb NOT NULL CHECK(jsonb_typeof(content)='object'),
 evidence_eligible boolean NOT NULL DEFAULT false CHECK(NOT evidence_eligible),
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id,organization_id), UNIQUE(tool_run_id,fact_id),
 FOREIGN KEY(tool_run_id,organization_id) REFERENCES tool_runs(id,organization_id),
 FOREIGN KEY(fact_id,organization_id) REFERENCES analysis_facts(id,organization_id)
);
CREATE INDEX analysis_fact_run_idx ON analysis_facts(organization_id,tool_run_id);
CREATE INDEX evidence_run_idx ON evidence_items(organization_id,tool_run_id);
CREATE TRIGGER fact_immutable BEFORE UPDATE OR DELETE ON analysis_facts FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE TRIGGER evidence_immutable BEFORE UPDATE OR DELETE ON evidence_items FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE FUNCTION check_fact_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r tool_runs; p uuid; f analysis_facts; e analysis_facts;
BEGIN
 SELECT * INTO r FROM tool_runs WHERE id=NEW.tool_run_id AND organization_id=NEW.organization_id FOR UPDATE;
 IF r.tool_name IS DISTINCT FROM 'match_frequency_evidence' OR r.status IS DISTINCT FROM 'running' THEN
  RAISE EXCEPTION 'Facts can only be registered by an active matching run'; END IF;
 IF TG_TABLE_NAME='analysis_facts' THEN
  IF NEW.measurement_revision_id IS DISTINCT FROM r.measurement_revision_id OR NEW.context_id IS DISTINCT FROM r.context_id
   OR NEW.input_sha256 IS DISTINCT FROM r.input_snapshot->>'input_sha256'
   OR NEW.root_file_object_id::text IS DISTINCT FROM r.input_snapshot->>'file_object_id' THEN
   RAISE EXCEPTION 'Fact root does not match accepted input'; END IF;
  FOREACH p IN ARRAY NEW.parent_fact_ids LOOP
   SELECT * INTO f FROM analysis_facts WHERE id=p AND organization_id=NEW.organization_id;
   IF f.tool_run_id IS DISTINCT FROM r.id THEN RAISE EXCEPTION 'Parent fact belongs to another execution'; END IF;
  END LOOP;
 ELSE
  SELECT * INTO e FROM analysis_facts WHERE id=NEW.fact_id AND organization_id=NEW.organization_id;
  IF e.tool_run_id IS DISTINCT FROM r.id OR e.kind IS DISTINCT FROM 'matching_relation' THEN
   RAISE EXCEPTION 'Evidence must reference this run matching relation'; END IF;
 END IF; RETURN NEW;
END $$;
CREATE TRIGGER fact_binding BEFORE INSERT ON analysis_facts FOR EACH ROW EXECUTE FUNCTION check_fact_binding();
CREATE TRIGGER evidence_binding BEFORE INSERT ON evidence_items FOR EACH ROW EXECUTE FUNCTION check_fact_binding();
CREATE FUNCTION check_evidence_completion() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r tool_runs; actual_facts integer; actual_evidence integer;
BEGIN
 IF TG_TABLE_NAME='tool_runs' THEN SELECT * INTO r FROM tool_runs WHERE id=NEW.id;
 ELSE SELECT * INTO r FROM tool_runs WHERE id=NEW.tool_run_id; END IF;
 IF r.tool_name<>'match_frequency_evidence' THEN RETURN NULL; END IF;
 SELECT count(*) INTO actual_facts FROM analysis_facts WHERE tool_run_id=r.id;
 SELECT count(*) INTO actual_evidence FROM evidence_items WHERE tool_run_id=r.id;
 IF r.status='completed' AND r.result_status='ok' THEN
  IF actual_facts IS DISTINCT FROM jsonb_array_length(r.result_metadata->'fact_refs')
   OR actual_evidence IS DISTINCT FROM jsonb_array_length(r.result_metadata->'evidence_refs') THEN
   RAISE EXCEPTION 'Matching graph is incomplete'; END IF;
 ELSIF actual_facts<>0 OR actual_evidence<>0 THEN RAISE EXCEPTION 'Unsuccessful run cannot register facts/evidence'; END IF;
 RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER matching_graph_completion AFTER INSERT OR UPDATE ON tool_runs
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_evidence_completion();
CREATE CONSTRAINT TRIGGER fact_completion AFTER INSERT ON analysis_facts
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_evidence_completion();
CREATE CONSTRAINT TRIGGER evidence_completion AFTER INSERT ON evidence_items
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_evidence_completion();
''')


def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
