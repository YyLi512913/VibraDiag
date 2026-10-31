"""Isolated numerical model graph and model-decision phase; no new business tables."""
from alembic import op
from sqlalchemy import text
revision='0021_numeric_scope_v2'
down_revision='0020_numeric_model_graph'


def upgrade():
    c=op.get_bind();base=c.execute(text('SELECT current_schema()')).scalar_one();b=c.dialect.identifier_preparer.quote(base)
    c.exec_driver_sql("""ALTER TABLE agent_graph_bindings DROP CONSTRAINT agent_graph_bindings_graph_version_check;
    ALTER TABLE agent_graph_bindings ADD CHECK(graph_version IN ('rules-graph-v1','rules-knowledge-v1','model-method-v1','model-numeric-v1','model-numeric-v2'));
    ALTER TABLE agent_model_rounds DROP CONSTRAINT agent_model_rounds_phase_check;
    ALTER TABLE agent_model_rounds ADD CHECK(phase IN ('decision','extractive_draft','numeric_decision','numeric_decision_v2'));""")
    c.exec_driver_sql(f"""CREATE OR REPLACE FUNCTION protect_agent_graph_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t {b}.agent_tasks;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'graph binding immutable'; END IF;
 SELECT * INTO t FROM {b}.agent_tasks WHERE id=NEW.task_id;
 IF t.id IS NULL OR t.organization_id IS DISTINCT FROM NEW.organization_id OR t.requested_by IS DISTINCT FROM NEW.requested_by
  OR t.input_fingerprint IS DISTINCT FROM NEW.input_fingerprint OR t.status IS DISTINCT FROM 'queued'
  OR NOT COALESCE((NEW.graph_version='rules-graph-v1' AND t.input_snapshot->>'executor'='rules_graph_v1')
    OR (NEW.graph_version='rules-knowledge-v1' AND t.input_snapshot->>'executor'='rules_knowledge_v1'
        AND t.goal='explain_method' AND t.input_snapshot->'roots'='[]'::jsonb)
    OR (NEW.graph_version='model-method-v1' AND t.input_snapshot->>'executor'='model_method_v1'
        AND t.goal='explain_method' AND t.measurement_revision_id IS NULL AND t.input_snapshot->'roots'='[]'::jsonb
        AND jsonb_typeof(t.input_snapshot->'model_configuration')='object')
    OR ((NEW.graph_version='model-numeric-v1' AND t.input_snapshot->>'executor'='model_numeric_v1'
            OR NEW.graph_version='model-numeric-v2' AND t.input_snapshot->>'executor'='model_numeric_v2')
        AND t.goal IN ('inspect_signal','bearing_observations','compare_measurements') AND t.measurement_revision_id IS NOT NULL
        AND jsonb_array_length(t.input_snapshot->'roots') BETWEEN 1 AND 2
        AND jsonb_typeof(t.input_snapshot->'model_configuration')='object'),false)
 THEN RAISE EXCEPTION 'graph binding mismatch'; END IF;
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION guard_agent_model_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t {b}.agent_tasks;
BEGIN
 SELECT * INTO t FROM {b}.agent_tasks WHERE id=NEW.task_id FOR SHARE;
 IF t.status IS DISTINCT FROM 'running'
  OR NOT COALESCE(t.goal='explain_method' OR (t.input_snapshot->>'executor' IN ('model_numeric_v1','model_numeric_v2')
      AND t.goal IN ('inspect_signal','bearing_observations','compare_measurements')),false)
  OR t.requested_by IS DISTINCT FROM NEW.actor_id OR t.organization_id IS DISTINCT FROM NEW.organization_id
  OR t.lease_token::text IS DISTINCT FROM current_setting('vibradiag.model_lease',true)
 THEN RAISE EXCEPTION 'model binding fenced'; END IF;
 RETURN NEW;
END $$;""")


def downgrade():raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
