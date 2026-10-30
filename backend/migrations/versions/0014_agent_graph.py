"""Bound task graphs and frozen checkpoint-postgres 3.2.0 schema (new empty tables)."""
from alembic import op
from sqlalchemy import text
revision='0014_agent_graph'
down_revision='0013_agent_tasks'


def upgrade():
    connection=op.get_bind();base=connection.execute(text('SELECT current_schema()')).scalar_one()
    quote=connection.dialect.identifier_preparer.quote
    namespace=base+'_agent_checkpoints';b=quote(base);n=quote(namespace)
    connection.exec_driver_sql(f'''CREATE TABLE agent_graph_bindings (
        task_id uuid PRIMARY KEY,organization_id uuid NOT NULL,requested_by uuid NOT NULL,
        thread_id text NOT NULL UNIQUE CHECK(thread_id ~ '^[a-f0-9-]{{36}}$'),
        graph_version text NOT NULL CHECK(graph_version='rules-graph-v1'),
        input_fingerprint text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY(task_id,organization_id) REFERENCES agent_tasks(id,organization_id),
        FOREIGN KEY(requested_by,organization_id) REFERENCES users(id,organization_id));
        CREATE FUNCTION protect_agent_graph_binding() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE t {b}.agent_tasks;
        BEGIN
          IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'graph binding immutable'; END IF;
          SELECT * INTO t FROM {b}.agent_tasks WHERE id=NEW.task_id;
          IF t.organization_id IS DISTINCT FROM NEW.organization_id OR t.requested_by IS DISTINCT FROM NEW.requested_by
             OR t.input_fingerprint IS DISTINCT FROM NEW.input_fingerprint OR t.status<>'queued'
             OR t.input_snapshot->>'executor' IS DISTINCT FROM 'rules_graph_v1'
          THEN RAISE EXCEPTION 'graph binding mismatch'; END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER agent_graph_binding_guard BEFORE INSERT OR UPDATE OR DELETE ON agent_graph_bindings
          FOR EACH ROW EXECUTE FUNCTION protect_agent_graph_binding();
        CREATE SCHEMA {n};''')
    old=connection.execute(text("SELECT current_setting('search_path')")).scalar_one()
    connection.exec_driver_sql(f'SET LOCAL search_path TO {n},{b}')
    # Frozen equivalent of the official ten migrations. Indexes are non-concurrent
    # because this migration creates new, empty tables in the same transaction.
    connection.exec_driver_sql('''
        CREATE TABLE checkpoint_migrations(v integer PRIMARY KEY);
        CREATE TABLE checkpoints(thread_id text NOT NULL,checkpoint_ns text NOT NULL DEFAULT '',checkpoint_id text NOT NULL,
            parent_checkpoint_id text,type text,checkpoint jsonb NOT NULL,metadata jsonb NOT NULL DEFAULT '{}',
            PRIMARY KEY(thread_id,checkpoint_ns,checkpoint_id));
        CREATE TABLE checkpoint_blobs(thread_id text NOT NULL,checkpoint_ns text NOT NULL DEFAULT '',channel text NOT NULL,
            version text NOT NULL,type text NOT NULL,blob bytea,PRIMARY KEY(thread_id,checkpoint_ns,channel,version));
        CREATE TABLE checkpoint_writes(thread_id text NOT NULL,checkpoint_ns text NOT NULL DEFAULT '',checkpoint_id text NOT NULL,
            task_id text NOT NULL,idx integer NOT NULL,channel text NOT NULL,type text,blob bytea NOT NULL,
            task_path text NOT NULL DEFAULT '',PRIMARY KEY(thread_id,checkpoint_ns,checkpoint_id,task_id,idx));
        CREATE INDEX checkpoints_thread_id_idx ON checkpoints(thread_id);
        CREATE INDEX checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id);
        CREATE INDEX checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id);
        INSERT INTO checkpoint_migrations SELECT generate_series(0,9);
    ''')
    connection.exec_driver_sql(f'''CREATE FUNCTION guard_graph_checkpoint() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE t {b}.agent_tasks;
        BEGIN
          IF TG_OP='DELETE' THEN RAISE EXCEPTION 'checkpoint deletion disabled'; END IF;
          SELECT a.* INTO t FROM {b}.agent_tasks a JOIN {b}.agent_graph_bindings g ON g.task_id=a.id
             WHERE g.thread_id=NEW.thread_id FOR SHARE OF a;
          IF t.id IS NULL OR t.status<>'running' OR t.lease_token::text IS DISTINCT FROM current_setting('vibradiag.graph_lease',true)
          THEN RAISE EXCEPTION 'checkpoint lease fenced'; END IF;
          RETURN NEW;
        END $$;''')
    for table in ('checkpoints','checkpoint_blobs','checkpoint_writes'):
        connection.exec_driver_sql(f'''ALTER TABLE {table} ADD FOREIGN KEY(thread_id) REFERENCES {b}.agent_graph_bindings(thread_id);
            ALTER TABLE {table} ADD CHECK(checkpoint_ns='');
            CREATE TRIGGER graph_checkpoint_guard BEFORE INSERT OR UPDATE OR DELETE ON {table}
              FOR EACH ROW EXECUTE FUNCTION guard_graph_checkpoint();''')
    connection.execute(text("SELECT set_config('search_path',:old,true)"),dict(old=old))


def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
