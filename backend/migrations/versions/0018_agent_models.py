"""Pinned task model bindings and fenced, immutable model-round receipts."""
from alembic import op
revision='0018_agent_models'
down_revision='0017_agent_knowledge'


def upgrade():
    op.get_bind().exec_driver_sql('''
CREATE TABLE agent_model_bindings (
 task_id uuid PRIMARY KEY,organization_id uuid NOT NULL,actor_id uuid NOT NULL,
 configuration jsonb NOT NULL,configuration_sha256 text NOT NULL CHECK(configuration_sha256 ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(task_id,organization_id,configuration_sha256),
 FOREIGN KEY(task_id,organization_id) REFERENCES agent_tasks(id,organization_id),
 FOREIGN KEY(actor_id,organization_id) REFERENCES users(id,organization_id)
);
CREATE TRIGGER model_binding_immutable BEFORE UPDATE OR DELETE ON agent_model_bindings FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE TABLE agent_model_rounds (
 id uuid PRIMARY KEY,task_id uuid NOT NULL,organization_id uuid NOT NULL,configuration_sha256 text NOT NULL,
 round_no integer NOT NULL CHECK(round_no BETWEEN 1 AND 8),phase text NOT NULL CHECK(phase IN ('decision','extractive_draft')),
 logical_key text NOT NULL CHECK(logical_key ~ '^[a-f0-9]{64}$'),lease_token uuid NOT NULL,
 context jsonb NOT NULL CHECK(octet_length(context::text)<=65536),context_sha256 text NOT NULL,
 output_schema jsonb NOT NULL,schema_sha256 text NOT NULL,prompt_version text NOT NULL,
 state text NOT NULL DEFAULT 'running' CHECK(state IN ('running','completed','failed','interrupted')),
 validated_output jsonb,output_sha256 text,raw_output_sha256 text,trace jsonb,error_code text,
 created_at timestamptz NOT NULL DEFAULT now(),finished_at timestamptz,
 UNIQUE(task_id,logical_key),UNIQUE(task_id,round_no),
 FOREIGN KEY(task_id,organization_id,configuration_sha256) REFERENCES agent_model_bindings(task_id,organization_id,configuration_sha256),
 CHECK((state='running')=(finished_at IS NULL)),
 CHECK((state='completed')=(validated_output IS NOT NULL AND output_sha256 IS NOT NULL)),
 CHECK(validated_output IS NULL OR octet_length(validated_output::text)<=16384),
 CHECK(trace IS NULL OR octet_length(trace::text)<=16384)
);
CREATE FUNCTION guard_agent_model_binding() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t agent_tasks;
BEGIN
 SELECT * INTO t FROM agent_tasks WHERE id=NEW.task_id FOR SHARE;
 IF t.status IS DISTINCT FROM 'running' OR t.goal IS DISTINCT FROM 'explain_method'
  OR t.requested_by IS DISTINCT FROM NEW.actor_id OR t.organization_id IS DISTINCT FROM NEW.organization_id
  OR t.lease_token::text IS DISTINCT FROM current_setting('vibradiag.model_lease',true)
 THEN RAISE EXCEPTION 'model binding fenced'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER model_binding_guard BEFORE INSERT ON agent_model_bindings FOR EACH ROW EXECUTE FUNCTION guard_agent_model_binding();
CREATE FUNCTION guard_agent_model_round() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE t agent_tasks;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'model history retained'; END IF;
 SELECT * INTO t FROM agent_tasks WHERE id=NEW.task_id FOR SHARE;
 IF TG_OP='UPDATE' THEN
  IF OLD.state<>'running' OR NEW.state='running' OR
   (to_jsonb(NEW)-ARRAY['state','validated_output','output_sha256','raw_output_sha256','trace','error_code','finished_at'])
   IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['state','validated_output','output_sha256','raw_output_sha256','trace','error_code','finished_at'])
  THEN RAISE EXCEPTION 'model round immutable'; END IF;
  IF NEW.state='interrupted' AND t.status<>'running' THEN RETURN NEW; END IF;
 ELSIF NEW.state<>'running' THEN RAISE EXCEPTION 'model round must reserve before dispatch';
 END IF;
 IF t.status IS DISTINCT FROM 'running' OR t.organization_id IS DISTINCT FROM NEW.organization_id
  OR t.lease_token IS DISTINCT FROM NEW.lease_token
  OR t.lease_token::text IS DISTINCT FROM current_setting('vibradiag.model_lease',true)
 THEN RAISE EXCEPTION 'model round lease fenced'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER model_round_guard BEFORE INSERT OR UPDATE OR DELETE ON agent_model_rounds FOR EACH ROW EXECUTE FUNCTION guard_agent_model_round();
''')


def downgrade():raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
