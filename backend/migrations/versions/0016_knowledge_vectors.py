"""Immutable dense index generations and authorized retrieval receipts."""
from alembic import op
from sqlalchemy import text
revision='0016_knowledge_vectors'
down_revision='0015_knowledge_documents'


def upgrade():
    connection=op.get_bind()
    installed=connection.execute(text("SELECT e.extversion,n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE extname='vector'")).first()
    if not installed or tuple(installed)!=('0.8.6','vibradiag_extensions'):
        raise RuntimeError('Run the explicit vector extension maintenance step before migration 0016')
    connection.exec_driver_sql('''
ALTER TABLE knowledge_chunks ADD UNIQUE(id,revision_id,organization_id);
CREATE TABLE knowledge_index_runs (
 id uuid PRIMARY KEY,organization_id uuid NOT NULL,revision_id uuid NOT NULL,created_by uuid NOT NULL,
 key_hash text NOT NULL CHECK(key_hash ~ '^[a-f0-9]{64}$'),request_hash text NOT NULL CHECK(request_hash ~ '^[a-f0-9]{64}$'),
 identity jsonb NOT NULL,identity_sha256 text NOT NULL CHECK(identity_sha256 ~ '^[a-f0-9]{64}$'),runtime_version text,
 source_sha256 text NOT NULL,text_sha256 text NOT NULL,chunk_manifest_sha256 text NOT NULL,chunk_policy text NOT NULL,
 chunk_count integer NOT NULL CHECK(chunk_count BETWEEN 1 AND 128),expected_document_version integer NOT NULL,
 expected_revision_version integer NOT NULL,expected_index_version integer NOT NULL CHECK(expected_index_version>=0),
 reason text NOT NULL,status text NOT NULL DEFAULT 'processing' CHECK(status IN ('processing','ready','failed')),
 row_version integer NOT NULL DEFAULT 1,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '120 seconds',completed_at timestamptz,
 vector_count integer NOT NULL DEFAULT 0,vector_sha256 text,error_code text,
 UNIQUE(id,revision_id,organization_id),UNIQUE(id,organization_id),UNIQUE(organization_id,created_by,key_hash),
 FOREIGN KEY(revision_id,organization_id) REFERENCES knowledge_revisions(id,organization_id),
 FOREIGN KEY(created_by,organization_id) REFERENCES users(id,organization_id),
 CHECK((status='processing')=(completed_at IS NULL)),
 CHECK((status='failed')=(error_code IS NOT NULL)),CHECK(vector_count BETWEEN 0 AND chunk_count),
 CHECK((status='ready')=(vector_sha256 IS NOT NULL)),CHECK(status<>'ready' OR vector_count=chunk_count),
 CHECK(vector_sha256 IS NULL OR vector_sha256 ~ '^[a-f0-9]{64}$'),CHECK(status<>'ready' OR runtime_version IS NOT NULL)
);
CREATE UNIQUE INDEX knowledge_one_processing_revision ON knowledge_index_runs(revision_id) WHERE status='processing';
CREATE TABLE knowledge_chunk_vectors (
 run_id uuid NOT NULL,organization_id uuid NOT NULL,revision_id uuid NOT NULL,chunk_id uuid NOT NULL,
 text_sha256 text NOT NULL,input_sha256 text NOT NULL,embedding vibradiag_extensions.vector(1024) NOT NULL,
 PRIMARY KEY(run_id,chunk_id),
 FOREIGN KEY(run_id,revision_id,organization_id) REFERENCES knowledge_index_runs(id,revision_id,organization_id),
 FOREIGN KEY(chunk_id,revision_id,organization_id) REFERENCES knowledge_chunks(id,revision_id,organization_id),
 CHECK(vibradiag_extensions.vector_dims(embedding)=1024),
 CHECK(vibradiag_extensions.vector_norm(embedding) BETWEEN 0.99999 AND 1.00001),
 CHECK(text_sha256 ~ '^[a-f0-9]{64}$' AND input_sha256=text_sha256)
);
CREATE TABLE knowledge_active_indexes (
 revision_id uuid PRIMARY KEY,organization_id uuid NOT NULL,run_id uuid NOT NULL UNIQUE,
 row_version integer NOT NULL CHECK(row_version>=1),changed_by uuid NOT NULL,changed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(run_id,revision_id,organization_id) REFERENCES knowledge_index_runs(id,revision_id,organization_id),
 FOREIGN KEY(changed_by,organization_id) REFERENCES users(id,organization_id)
);
CREATE TABLE knowledge_retrieval_runs (
 id uuid PRIMARY KEY,organization_id uuid NOT NULL,actor_id uuid NOT NULL,query text NOT NULL CHECK(length(query) BETWEEN 1 AND 2000),
 query_sha256 text NOT NULL,filters jsonb NOT NULL,identity_sha256 text NOT NULL,runtime_version text,
 model_called boolean NOT NULL,matches jsonb NOT NULL,eligible_count integer NOT NULL CHECK(eligible_count BETWEEN 0 AND 10000),
 timing jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(actor_id,organization_id) REFERENCES users(id,organization_id)
);
CREATE INDEX knowledge_index_revision ON knowledge_index_runs(organization_id,revision_id,created_at DESC,id);
CREATE INDEX knowledge_retrieval_actor ON knowledge_retrieval_runs(organization_id,actor_id,created_at DESC,id);
CREATE TRIGGER knowledge_vectors_immutable BEFORE UPDATE OR DELETE ON knowledge_chunk_vectors FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE TRIGGER knowledge_retrieval_immutable BEFORE UPDATE OR DELETE ON knowledge_retrieval_runs FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE FUNCTION protect_knowledge_index_run() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE amount integer;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'index history retained'; END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.status<>'processing' OR NEW.row_version<>1 OR NEW.vector_count<>0 THEN RAISE EXCEPTION 'index must begin processing'; END IF;
 ELSE
  IF (to_jsonb(NEW)-ARRAY['status','row_version','completed_at','vector_count','vector_sha256','error_code','runtime_version'])
    IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','row_version','completed_at','vector_count','vector_sha256','error_code','runtime_version'])
    OR OLD.status<>'processing' OR NEW.status NOT IN ('ready','failed') OR NEW.row_version<>OLD.row_version+1
  THEN RAISE EXCEPTION 'invalid index transition'; END IF;
  IF NEW.status='ready' THEN
   IF clock_timestamp()>=NEW.expires_at THEN RAISE EXCEPTION 'index deadline expired'; END IF;
   SELECT count(*) INTO amount FROM knowledge_chunk_vectors WHERE run_id=NEW.id;
   IF amount<>NEW.chunk_count THEN RAISE EXCEPTION 'incomplete vector generation'; END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER knowledge_index_run_guard BEFORE INSERT OR UPDATE OR DELETE ON knowledge_index_runs FOR EACH ROW EXECUTE FUNCTION protect_knowledge_index_run();
CREATE FUNCTION protect_knowledge_vector_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r knowledge_index_runs;c knowledge_chunks;
BEGIN
 SELECT * INTO r FROM knowledge_index_runs WHERE id=NEW.run_id FOR SHARE;
 SELECT * INTO c FROM knowledge_chunks WHERE id=NEW.chunk_id;
 IF r.status IS DISTINCT FROM 'processing' OR r.revision_id IS DISTINCT FROM NEW.revision_id
   OR c.revision_id IS DISTINCT FROM NEW.revision_id OR c.text_sha256 IS DISTINCT FROM NEW.text_sha256
 THEN RAISE EXCEPTION 'vector source/generation mismatch'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER knowledge_vector_insert_guard BEFORE INSERT ON knowledge_chunk_vectors FOR EACH ROW EXECUTE FUNCTION protect_knowledge_vector_insert();
CREATE FUNCTION protect_knowledge_active_index() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r knowledge_index_runs;v knowledge_revisions;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'index head retained'; END IF;
 IF TG_OP='UPDATE' AND (NEW.revision_id<>OLD.revision_id OR NEW.organization_id<>OLD.organization_id OR NEW.row_version<>OLD.row_version+1)
 THEN RAISE EXCEPTION 'invalid index head transition'; END IF;
 IF TG_OP='INSERT' AND NEW.row_version<>1 THEN RAISE EXCEPTION 'invalid initial index head'; END IF;
 SELECT * INTO r FROM knowledge_index_runs WHERE id=NEW.run_id;
 SELECT * INTO v FROM knowledge_revisions WHERE id=NEW.revision_id;
 IF r.status IS DISTINCT FROM 'ready' OR v.status IS DISTINCT FROM 'published'
  OR NOT EXISTS(SELECT 1 FROM knowledge_documents WHERE published_revision_id=v.id AND id=v.document_id)
 THEN RAISE EXCEPTION 'active index must reference a ready published generation'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER knowledge_active_index_guard BEFORE INSERT OR UPDATE OR DELETE ON knowledge_active_indexes FOR EACH ROW EXECUTE FUNCTION protect_knowledge_active_index();
''')


def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
