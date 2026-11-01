"""Optional bounded validation metadata; legacy receipts stay null and immutable."""
from alembic import op
from sqlalchemy import text
revision='0022_model_diagnostics'
down_revision='0021_numeric_scope_v2'


def upgrade():
    c=op.get_bind();base=c.execute(text('SELECT current_schema()')).scalar_one();b=c.dialect.identifier_preparer.quote(base)
    c.exec_driver_sql("""ALTER TABLE agent_model_rounds ADD COLUMN validation_diagnostics jsonb;
    ALTER TABLE agent_model_rounds ADD CONSTRAINT model_validation_diagnostics_check CHECK (
      validation_diagnostics IS NULL OR (
        state='failed' AND error_code='MODEL_OUTPUT_SCHEMA_INVALID'
        AND octet_length(validation_diagnostics::text)<=4096
        AND jsonb_typeof(validation_diagnostics)='object'
        AND validation_diagnostics->>'version'='model-validation-diagnostics-v1'
        AND validation_diagnostics ?& ARRAY['version','top_level_type','action_kind','known_fields','unknown_field_count','error_count','issues','truncated']
        AND (validation_diagnostics-ARRAY['version','top_level_type','action_kind','known_fields','unknown_field_count','error_count','issues','truncated'])='{}'::jsonb
        AND CASE WHEN jsonb_typeof(validation_diagnostics->'issues')='array' THEN jsonb_array_length(validation_diagnostics->'issues') BETWEEN 1 AND 8 ELSE false END
      ));""")
    c.exec_driver_sql(f"""CREATE OR REPLACE FUNCTION guard_agent_model_round() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t {b}.agent_tasks;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'model history retained'; END IF;
 SELECT * INTO t FROM {b}.agent_tasks WHERE id=NEW.task_id FOR SHARE;
 IF TG_OP='UPDATE' THEN
  IF OLD.state<>'running' OR NEW.state='running' OR
   (to_jsonb(NEW)-ARRAY['state','validated_output','output_sha256','raw_output_sha256','trace','error_code','finished_at','validation_diagnostics'])
   IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','validated_output','output_sha256','raw_output_sha256','trace','error_code','finished_at','validation_diagnostics'])
  THEN RAISE EXCEPTION 'model round immutable'; END IF;
  IF NEW.state='interrupted' AND t.status<>'running' THEN RETURN NEW; END IF;
 ELSIF NEW.state<>'running' THEN RAISE EXCEPTION 'model round must reserve before dispatch';
 END IF;
 IF t.status IS DISTINCT FROM 'running' OR t.organization_id IS DISTINCT FROM NEW.organization_id
  OR t.lease_token IS DISTINCT FROM NEW.lease_token
  OR t.lease_token::text IS DISTINCT FROM current_setting('vibradiag.model_lease',true)
 THEN RAISE EXCEPTION 'model round lease fenced'; END IF;
 RETURN NEW;
END $$;""")


def downgrade():raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
