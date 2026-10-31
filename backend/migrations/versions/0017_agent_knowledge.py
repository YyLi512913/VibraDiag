"""Versioned method-only knowledge graph and idempotent fenced retrieval attachment."""
from alembic import op
from sqlalchemy import text
revision='0017_agent_knowledge'
down_revision='0016_knowledge_vectors'


def upgrade():
    c=op.get_bind();base=c.execute(text('SELECT current_schema()')).scalar_one();b=c.dialect.identifier_preparer.quote(base)
    c.exec_driver_sql('''
ALTER TABLE agent_calls DROP CONSTRAINT agent_calls_action_name_check;
ALTER TABLE agent_calls ADD CHECK(action_name IN ('analyze_basic','analyze_envelope','calculate_bearing_frequencies','match_frequency_evidence','compare_measurements','search_published_knowledge'));
ALTER TABLE agent_calls DROP CONSTRAINT agent_calls_resource_kind_check;
ALTER TABLE agent_calls ADD CHECK(resource_kind IN ('analysis','tool','knowledge_retrieval'));
ALTER TABLE agent_calls ADD CHECK((action_name='search_published_knowledge')=(resource_kind='knowledge_retrieval'));
ALTER TABLE agent_calls ADD UNIQUE(id,task_id,organization_id);
ALTER TABLE knowledge_retrieval_runs ADD COLUMN agent_task_id uuid;
ALTER TABLE knowledge_retrieval_runs ADD COLUMN agent_call_id uuid UNIQUE;
ALTER TABLE knowledge_retrieval_runs ADD CHECK((agent_task_id IS NULL)=(agent_call_id IS NULL));
ALTER TABLE knowledge_retrieval_runs ADD FOREIGN KEY(agent_call_id,agent_task_id,organization_id) REFERENCES agent_calls(id,task_id,organization_id);
ALTER TABLE agent_graph_bindings DROP CONSTRAINT agent_graph_bindings_graph_version_check;
ALTER TABLE agent_graph_bindings ADD CHECK(graph_version IN ('rules-graph-v1','rules-knowledge-v1'));
''')
    c.exec_driver_sql(f'''CREATE OR REPLACE FUNCTION protect_agent_graph_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t {b}.agent_tasks;
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'graph binding immutable'; END IF;
 SELECT * INTO t FROM {b}.agent_tasks WHERE id=NEW.task_id;
 IF t.organization_id IS DISTINCT FROM NEW.organization_id OR t.requested_by IS DISTINCT FROM NEW.requested_by
  OR t.input_fingerprint IS DISTINCT FROM NEW.input_fingerprint OR t.status<>'queued'
  OR NOT ((NEW.graph_version='rules-graph-v1' AND t.input_snapshot->>'executor'='rules_graph_v1')
    OR (NEW.graph_version='rules-knowledge-v1' AND t.input_snapshot->>'executor'='rules_knowledge_v1'
       AND t.goal='explain_method' AND t.input_snapshot->'roots'='[]'::jsonb))
 THEN RAISE EXCEPTION 'graph binding mismatch'; END IF;
 RETURN NEW;
END $$;
CREATE FUNCTION guard_agent_knowledge_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t {b}.agent_tasks;a {b}.agent_calls;
BEGIN
 IF NEW.agent_task_id IS NULL THEN RETURN NEW; END IF;
 SELECT * INTO t FROM {b}.agent_tasks WHERE id=NEW.agent_task_id FOR SHARE;
 SELECT * INTO a FROM {b}.agent_calls WHERE id=NEW.agent_call_id FOR SHARE;
 IF t.status IS DISTINCT FROM 'running' OR t.goal IS DISTINCT FROM 'explain_method'
  OR t.input_snapshot->>'executor' IS DISTINCT FROM 'rules_knowledge_v1'
  OR t.lease_token::text IS DISTINCT FROM current_setting('vibradiag.knowledge_lease',true)
  OR t.requested_by IS DISTINCT FROM NEW.actor_id OR t.organization_id IS DISTINCT FROM NEW.organization_id
  OR a.state IS DISTINCT FROM 'running' OR a.action_name IS DISTINCT FROM 'search_published_knowledge'
  OR a.task_id IS DISTINCT FROM t.id OR NEW.query IS DISTINCT FROM a.payload->>'query'
  OR NEW.filters IS DISTINCT FROM (a.payload-'query') OR NEW.filters->>'include_developer' IS DISTINCT FROM 'false'
 THEN RAISE EXCEPTION 'knowledge task receipt fenced'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER agent_knowledge_receipt_guard BEFORE INSERT ON knowledge_retrieval_runs
 FOR EACH ROW EXECUTE FUNCTION guard_agent_knowledge_receipt();
''')


def downgrade():raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
