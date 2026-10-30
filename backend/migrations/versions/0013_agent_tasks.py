"""Persistent exploratory tasks, command idempotency and fenced tool ledger."""
from alembic import op
revision='0013_agent_tasks'
down_revision='0012_comparison_runs'


def upgrade():
    op.get_bind().exec_driver_sql('''
CREATE TABLE agent_tasks (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL, requested_by uuid NOT NULL, session_id uuid NOT NULL,
 measurement_revision_id uuid, before_revision_id uuid,
 goal text NOT NULL CHECK(goal IN ('inspect_signal','bearing_observations','compare_measurements','explain_method')),
 payload jsonb NOT NULL, payload_hash text NOT NULL CHECK(payload_hash ~ '^[a-f0-9]{64}$'),
 input_snapshot jsonb NOT NULL, input_fingerprint text NOT NULL CHECK(input_fingerprint ~ '^[a-f0-9]{64}$'),
 status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','waiting_information','completed',
  'completed_degraded','refused','failed','cancelled','interrupted','superseded','budget_exhausted','timed_out')),
 version integer NOT NULL DEFAULT 1 CHECK(version>=1),
 budget jsonb NOT NULL DEFAULT '{"policy_version":"agent-budget-v1","active_ms":0,"reservations":[]}',
 selection jsonb NOT NULL DEFAULT '{}', question jsonb, draft jsonb, error_code text,
 resume_count integer NOT NULL DEFAULT 0 CHECK(resume_count BETWEEN 0 AND 20),
 lease_token uuid, last_tick_at timestamptz, not_before timestamptz NOT NULL DEFAULT now(),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 UNIQUE(id,organization_id),
 FOREIGN KEY(requested_by,organization_id) REFERENCES users(id,organization_id),
 FOREIGN KEY(session_id) REFERENCES auth_sessions(id),
 FOREIGN KEY(measurement_revision_id,organization_id) REFERENCES measurement_revisions(id,organization_id),
 FOREIGN KEY(before_revision_id,organization_id) REFERENCES measurement_revisions(id,organization_id),
 CHECK((goal='explain_method' OR measurement_revision_id IS NOT NULL)
   AND ((goal='compare_measurements')=(before_revision_id IS NOT NULL))),
 CHECK((status='running' AND lease_token IS NOT NULL AND last_tick_at IS NOT NULL)
   OR (status<>'running' AND lease_token IS NULL AND last_tick_at IS NULL)),
 CHECK((status IN ('waiting_information','interrupted'))=(question IS NOT NULL)),
 CHECK((status IN ('completed','completed_degraded','refused','failed','cancelled','superseded','budget_exhausted','timed_out'))=(finished_at IS NOT NULL)),
 CHECK(draft IS NULL OR status IN ('completed','completed_degraded','refused')),
 CHECK(jsonb_typeof(budget)='object' AND budget->>'policy_version'='agent-budget-v1'
   AND (budget->>'active_ms')::numeric BETWEEN 0 AND 120000
   AND jsonb_typeof(budget->'reservations')='array' AND jsonb_array_length(budget->'reservations')<=28)
);
CREATE INDEX agent_tasks_queue ON agent_tasks(not_before,created_at,id) WHERE status='queued';
CREATE INDEX agent_tasks_owner ON agent_tasks(organization_id,requested_by,created_at,id);
CREATE TABLE agent_commands (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL, actor_id uuid NOT NULL, task_id uuid NOT NULL,
 operation text NOT NULL CHECK(operation IN ('create','resume','cancel')),
 key_hash text NOT NULL CHECK(key_hash ~ '^[a-f0-9]{64}$'), request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(organization_id,actor_id,key_hash),
 FOREIGN KEY(actor_id,organization_id) REFERENCES users(id,organization_id),
 FOREIGN KEY(task_id,organization_id) REFERENCES agent_tasks(id,organization_id)
);
CREATE TRIGGER agent_command_immutable BEFORE UPDATE OR DELETE ON agent_commands
 FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE TABLE agent_calls (
 id uuid PRIMARY KEY, task_id uuid NOT NULL, organization_id uuid NOT NULL,
 call_no integer NOT NULL CHECK(call_no BETWEEN 1 AND 20), operation_key text NOT NULL, logical_key text NOT NULL,
 action_name text NOT NULL CHECK(action_name IN ('analyze_basic','analyze_envelope','calculate_bearing_frequencies','match_frequency_evidence','compare_measurements')),
 payload jsonb NOT NULL, attempt_epoch integer NOT NULL CHECK(attempt_epoch>=0),
 state text NOT NULL CHECK(state IN ('running','completed','unavailable','failed','interrupted')),
 resource_kind text NOT NULL CHECK(resource_kind IN ('analysis','tool')), resource_id uuid,
 result_summary jsonb, error_code text, created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 UNIQUE(task_id,logical_key), UNIQUE(task_id,call_no),
 FOREIGN KEY(task_id,organization_id) REFERENCES agent_tasks(id,organization_id),
 CHECK(operation_key ~ '^[a-f0-9]{64}$' AND logical_key ~ '^[a-f0-9]{64}$'),
 CHECK((state='running')=(finished_at IS NULL)),
 CHECK(state NOT IN ('completed','unavailable') OR resource_id IS NOT NULL)
);
CREATE TABLE agent_events (
 id uuid PRIMARY KEY, task_id uuid NOT NULL, organization_id uuid NOT NULL, task_version integer NOT NULL,
 kind text NOT NULL, details jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(task_id,organization_id) REFERENCES agent_tasks(id,organization_id)
);
CREATE INDEX agent_events_task ON agent_events(task_id,created_at,id);
CREATE TRIGGER agent_event_immutable BEFORE UPDATE OR DELETE ON agent_events
 FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE FUNCTION protect_agent_task() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s auth_sessions;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'agent task history immutable'; END IF;
 SELECT * INTO s FROM auth_sessions WHERE id=NEW.session_id;
 IF s.user_id IS DISTINCT FROM NEW.requested_by OR s.organization_id IS DISTINCT FROM NEW.organization_id
 THEN RAISE EXCEPTION 'agent session binding mismatch'; END IF;
 IF TG_OP='UPDATE' THEN
  IF ROW(NEW.id,NEW.organization_id,NEW.requested_by,NEW.measurement_revision_id,NEW.before_revision_id,
    NEW.goal,NEW.payload,NEW.payload_hash,NEW.input_snapshot,NEW.input_fingerprint,NEW.created_at)
   IS DISTINCT FROM ROW(OLD.id,OLD.organization_id,OLD.requested_by,OLD.measurement_revision_id,OLD.before_revision_id,
    OLD.goal,OLD.payload,OLD.payload_hash,OLD.input_snapshot,OLD.input_fingerprint,OLD.created_at)
   THEN RAISE EXCEPTION 'agent input snapshot immutable'; END IF;
  IF OLD.status IN ('completed','completed_degraded','refused','failed','cancelled','superseded','budget_exhausted','timed_out')
   THEN RAISE EXCEPTION 'agent terminal state immutable'; END IF;
  IF NEW.version<>OLD.version+1 THEN RAISE EXCEPTION 'agent version must advance'; END IF;
  IF NEW.session_id<>OLD.session_id AND NOT (OLD.status IN ('waiting_information','interrupted') AND NEW.status='queued')
   THEN RAISE EXCEPTION 'session rebind only through resume'; END IF;
  IF OLD.status='queued' AND NEW.status NOT IN ('running','cancelled','superseded','budget_exhausted')
   THEN RAISE EXCEPTION 'invalid queued transition'; END IF;
  IF OLD.status IN ('waiting_information','interrupted') AND NEW.status NOT IN ('queued','cancelled','superseded','budget_exhausted')
   THEN RAISE EXCEPTION 'invalid waiting transition'; END IF;
  IF OLD.status='running' AND NEW.status='running' AND NEW.lease_token<>OLD.lease_token
   THEN RAISE EXCEPTION 'running lease immutable'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER agent_task_guard BEFORE INSERT OR UPDATE OR DELETE ON agent_tasks
 FOR EACH ROW EXECUTE FUNCTION protect_agent_task();
CREATE FUNCTION protect_agent_call() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'agent call history immutable'; END IF;
 IF ROW(NEW.id,NEW.task_id,NEW.organization_id,NEW.call_no,NEW.operation_key,NEW.logical_key,NEW.action_name,
  NEW.payload,NEW.attempt_epoch,NEW.resource_kind,NEW.created_at)
  IS DISTINCT FROM ROW(OLD.id,OLD.task_id,OLD.organization_id,OLD.call_no,OLD.operation_key,OLD.logical_key,OLD.action_name,
  OLD.payload,OLD.attempt_epoch,OLD.resource_kind,OLD.created_at)
 THEN RAISE EXCEPTION 'agent call identity immutable'; END IF;
 IF OLD.state IN ('completed','unavailable') THEN RAISE EXCEPTION 'completed agent call immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER agent_call_guard BEFORE UPDATE OR DELETE ON agent_calls
 FOR EACH ROW EXECUTE FUNCTION protect_agent_call();
''')


def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
