"""Versioned explicit-field method graph; old graphs and model phases are retained."""
from alembic import op
from sqlalchemy import text
revision='0024_method_model_v2'
down_revision='0023_diagnostics_null_guard'


def upgrade():
    c=op.get_bind();base=c.execute(text('SELECT current_schema()')).scalar_one();b=c.dialect.identifier_preparer.quote(base)
    c.exec_driver_sql("""ALTER TABLE agent_graph_bindings DROP CONSTRAINT agent_graph_bindings_graph_version_check;
    ALTER TABLE agent_graph_bindings ADD CHECK(graph_version IN ('rules-graph-v1','rules-knowledge-v1','model-method-v1','model-method-v2','model-numeric-v1','model-numeric-v2'));
    ALTER TABLE agent_model_rounds DROP CONSTRAINT agent_model_rounds_phase_check;
    ALTER TABLE agent_model_rounds ADD CHECK(phase IN ('decision','extractive_draft','decision_method_v2','extractive_method_v2','numeric_decision','numeric_decision_v2'));""")
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
    OR (((NEW.graph_version='model-method-v1' AND t.input_snapshot->>'executor'='model_method_v1')
         OR (NEW.graph_version='model-method-v2' AND t.input_snapshot->>'executor'='model_method_v2'))
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
CREATE OR REPLACE FUNCTION guard_agent_knowledge_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t {b}.agent_tasks;a {b}.agent_calls;
BEGIN
 IF NEW.agent_task_id IS NULL THEN RETURN NEW; END IF;
 SELECT * INTO t FROM {b}.agent_tasks WHERE id=NEW.agent_task_id FOR SHARE;
 SELECT * INTO a FROM {b}.agent_calls WHERE id=NEW.agent_call_id FOR SHARE;
 IF t.status IS DISTINCT FROM 'running' OR t.goal IS DISTINCT FROM 'explain_method'
  OR t.input_snapshot->>'executor' NOT IN ('rules_knowledge_v1','model_method_v1','model_method_v2')
  OR t.lease_token::text IS DISTINCT FROM current_setting('vibradiag.knowledge_lease',true)
  OR t.requested_by IS DISTINCT FROM NEW.actor_id OR t.organization_id IS DISTINCT FROM NEW.organization_id
  OR a.state IS DISTINCT FROM 'running' OR a.action_name IS DISTINCT FROM 'search_published_knowledge'
  OR a.task_id IS DISTINCT FROM t.id OR NEW.query IS DISTINCT FROM a.payload->>'query'
  OR NEW.filters IS DISTINCT FROM (a.payload-'query') OR NEW.filters->>'include_developer' IS DISTINCT FROM 'false'
 THEN RAISE EXCEPTION 'knowledge task receipt fenced'; END IF;
 RETURN NEW;
END $$;""")


def downgrade():raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
