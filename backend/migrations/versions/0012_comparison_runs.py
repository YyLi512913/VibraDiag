"""Immutable comparison declarations and two-input execution binding."""
from alembic import op
revision='0012_comparison_runs'
down_revision='0011_matching_evidence'


def upgrade():
    op.get_bind().exec_driver_sql('''
CREATE TABLE comparison_declarations (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL,
 before_revision_id uuid NOT NULL, after_revision_id uuid NOT NULL,
 before_context_id uuid NOT NULL, after_context_id uuid NOT NULL,
 before_basic_id uuid NOT NULL, after_basic_id uuid NOT NULL,
 before_envelope_id uuid, after_envelope_id uuid,
 declaration jsonb NOT NULL CHECK(jsonb_typeof(declaration)='object'),
 declaration_sha256 text NOT NULL CHECK(declaration_sha256 ~ '^[a-f0-9]{64}$'),
 parents jsonb NOT NULL CHECK(jsonb_typeof(parents)='object'),
 checked_by uuid NOT NULL, checked_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,organization_id), UNIQUE(id,before_revision_id,after_revision_id,organization_id),
 FOREIGN KEY(before_revision_id,organization_id) REFERENCES measurement_revisions(id,organization_id),
 FOREIGN KEY(after_revision_id,organization_id) REFERENCES measurement_revisions(id,organization_id),
 FOREIGN KEY(before_context_id,organization_id) REFERENCES diagnostic_contexts(id,organization_id),
 FOREIGN KEY(after_context_id,organization_id) REFERENCES diagnostic_contexts(id,organization_id),
 FOREIGN KEY(before_basic_id,organization_id) REFERENCES analysis_runs(id,organization_id),
 FOREIGN KEY(after_basic_id,organization_id) REFERENCES analysis_runs(id,organization_id),
 FOREIGN KEY(before_envelope_id,organization_id) REFERENCES tool_runs(id,organization_id),
 FOREIGN KEY(after_envelope_id,organization_id) REFERENCES tool_runs(id,organization_id),
 FOREIGN KEY(checked_by,organization_id) REFERENCES users(id,organization_id)
);
CREATE INDEX comparison_declaration_after_idx ON comparison_declarations(organization_id,after_revision_id,checked_at,id);
CREATE TRIGGER comparison_declaration_immutable BEFORE UPDATE OR DELETE ON comparison_declarations
 FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE FUNCTION bind_comparison_declaration() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE label text; revision_id uuid; context_id uuid; basic_id uuid; envelope_id uuid;
 c diagnostic_contexts; b analysis_runs; e tool_runs;
BEGIN
 FOREACH label IN ARRAY ARRAY['before','after'] LOOP
  IF label='before' THEN revision_id:=NEW.before_revision_id; context_id:=NEW.before_context_id;
   basic_id:=NEW.before_basic_id; envelope_id:=NEW.before_envelope_id;
  ELSE revision_id:=NEW.after_revision_id; context_id:=NEW.after_context_id;
   basic_id:=NEW.after_basic_id; envelope_id:=NEW.after_envelope_id; END IF;
  SELECT * INTO c FROM diagnostic_contexts WHERE id=context_id AND organization_id=NEW.organization_id;
  SELECT * INTO b FROM analysis_runs WHERE id=basic_id AND organization_id=NEW.organization_id;
  IF c.measurement_revision_id IS DISTINCT FROM revision_id OR b.measurement_revision_id IS DISTINCT FROM revision_id
   OR b.status IS DISTINCT FROM 'completed' THEN RAISE EXCEPTION 'Comparison declaration input/context/basic mismatch'; END IF;
  IF envelope_id IS NOT NULL THEN
   SELECT * INTO e FROM tool_runs WHERE id=envelope_id AND organization_id=NEW.organization_id;
   IF e.measurement_revision_id IS DISTINCT FROM revision_id OR e.context_id IS DISTINCT FROM context_id
    OR e.tool_name IS DISTINCT FROM 'analyze_envelope' OR e.status IS DISTINCT FROM 'completed'
    OR e.result_status IS DISTINCT FROM 'ok' THEN RAISE EXCEPTION 'Comparison envelope parent mismatch'; END IF;
  END IF;
 END LOOP; RETURN NEW;
END $$;
CREATE TRIGGER comparison_declaration_binding BEFORE INSERT ON comparison_declarations
 FOR EACH ROW EXECUTE FUNCTION bind_comparison_declaration();
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='tool_runs'::regclass
  AND contype='c' AND pg_get_constraintdef(oid) LIKE '%%tool_name%%' LOOP
  EXECUTE format('ALTER TABLE tool_runs DROP CONSTRAINT %%I',c.conname);
 END LOOP;
END $$;
ALTER TABLE tool_runs ADD COLUMN comparison_declaration_id uuid, ADD COLUMN before_revision_id uuid, ADD COLUMN before_confirmation_id uuid,
 ADD FOREIGN KEY(comparison_declaration_id,before_revision_id,measurement_revision_id,organization_id)
  REFERENCES comparison_declarations(id,before_revision_id,after_revision_id,organization_id),
 ADD FOREIGN KEY(before_confirmation_id,before_revision_id,organization_id) REFERENCES input_confirmations(id,revision_id,organization_id),
 ADD CONSTRAINT tool_names CHECK(tool_name IN ('analyze_envelope','calculate_bearing_frequencies','match_frequency_evidence','compare_measurements')),
 ADD CONSTRAINT tool_input_shape CHECK(
  (tool_name='analyze_envelope' AND profile_id IS NOT NULL AND envelope_run_id IS NULL AND frequency_run_id IS NULL AND basic_analysis_id IS NULL
   AND comparison_declaration_id IS NULL AND before_revision_id IS NULL AND before_confirmation_id IS NULL)
  OR (tool_name='calculate_bearing_frequencies' AND context_id IS NOT NULL AND profile_id IS NULL AND envelope_run_id IS NULL AND frequency_run_id IS NULL AND basic_analysis_id IS NULL
   AND comparison_declaration_id IS NULL AND before_revision_id IS NULL AND before_confirmation_id IS NULL)
  OR (tool_name='match_frequency_evidence' AND context_id IS NOT NULL AND profile_id IS NULL AND envelope_run_id IS NOT NULL AND frequency_run_id IS NOT NULL AND basic_analysis_id IS NOT NULL
   AND comparison_declaration_id IS NULL AND before_revision_id IS NULL AND before_confirmation_id IS NULL)
  OR (tool_name='compare_measurements' AND context_id IS NOT NULL AND profile_id IS NULL AND envelope_run_id IS NULL AND frequency_run_id IS NULL AND basic_analysis_id IS NULL
   AND comparison_declaration_id IS NOT NULL AND before_revision_id IS NOT NULL AND before_confirmation_id IS NOT NULL));
CREATE OR REPLACE FUNCTION protect_tool_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR OLD.status<>'running' THEN RAISE EXCEPTION 'Tool execution history is immutable'; END IF;
 IF (NEW.id,NEW.organization_id,NEW.measurement_revision_id,NEW.confirmation_id,NEW.context_id,NEW.profile_id,
     NEW.requested_by,NEW.tool_name,NEW.tool_version,NEW.retry_of,NEW.input_fingerprint,NEW.input_snapshot,NEW.started_at,
     NEW.envelope_run_id,NEW.frequency_run_id,NEW.basic_analysis_id,NEW.comparison_declaration_id,NEW.before_revision_id,NEW.before_confirmation_id)
  IS DISTINCT FROM
    (OLD.id,OLD.organization_id,OLD.measurement_revision_id,OLD.confirmation_id,OLD.context_id,OLD.profile_id,
     OLD.requested_by,OLD.tool_name,OLD.tool_version,OLD.retry_of,OLD.input_fingerprint,OLD.input_snapshot,OLD.started_at,
     OLD.envelope_run_id,OLD.frequency_run_id,OLD.basic_analysis_id,OLD.comparison_declaration_id,OLD.before_revision_id,OLD.before_confirmation_id) THEN
  RAISE EXCEPTION 'Accepted tool snapshot is immutable'; END IF; RETURN NEW;
END $$;
CREATE FUNCTION bind_comparison_run() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE d comparison_declarations;
BEGIN
 IF NEW.tool_name<>'compare_measurements' THEN RETURN NEW; END IF;
 SELECT * INTO d FROM comparison_declarations WHERE id=NEW.comparison_declaration_id AND organization_id=NEW.organization_id;
 IF d.before_revision_id IS DISTINCT FROM NEW.before_revision_id OR d.after_revision_id IS DISTINCT FROM NEW.measurement_revision_id
  OR d.after_context_id IS DISTINCT FROM NEW.context_id THEN RAISE EXCEPTION 'Comparison run differs from declaration'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER comparison_run_binding BEFORE INSERT ON tool_runs FOR EACH ROW EXECUTE FUNCTION bind_comparison_run();
''')


def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
