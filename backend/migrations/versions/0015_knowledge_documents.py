"""Immutable knowledge versions/anchors with explicit review capability and publication lifecycle."""
from alembic import op
revision='0015_knowledge_documents'
down_revision='0014_agent_graph'


def upgrade():
    op.get_bind().exec_driver_sql('''
CREATE TABLE knowledge_documents (
 id uuid PRIMARY KEY,organization_id uuid NOT NULL,owner_id uuid NOT NULL,
 visibility text NOT NULL CHECK(visibility IN ('organization','asset')),asset_id uuid,
 current_revision_id uuid,published_revision_id uuid,row_version integer NOT NULL DEFAULT 1 CHECK(row_version>=1),
 created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(id,organization_id),FOREIGN KEY(owner_id,organization_id) REFERENCES users(id,organization_id),
 FOREIGN KEY(asset_id,organization_id) REFERENCES assets(id,organization_id),
 CHECK((visibility='asset')=(asset_id IS NOT NULL))
);
CREATE TABLE knowledge_revisions (
 id uuid PRIMARY KEY,organization_id uuid NOT NULL,document_id uuid NOT NULL,revision_no integer NOT NULL CHECK(revision_no>=1),
 created_by uuid NOT NULL,original_filename text NOT NULL,format text NOT NULL CHECK(format IN ('md','txt')),
 original_bytes bytea NOT NULL CHECK(octet_length(original_bytes) BETWEEN 1 AND 5242880),
 source_sha256 text NOT NULL CHECK(source_sha256 ~ '^[a-f0-9]{64}$'),
 text_content text NOT NULL,text_sha256 text NOT NULL CHECK(text_sha256 ~ '^[a-f0-9]{64}$'),
 metadata jsonb NOT NULL,metadata_sha256 text NOT NULL CHECK(metadata_sha256 ~ '^[a-f0-9]{64}$'),
 chunk_policy text NOT NULL CHECK(chunk_policy='knowledge-lines-v1'),chunk_count integer NOT NULL CHECK(chunk_count BETWEEN 1 AND 4096),
 reason text NOT NULL,status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','pending_review','published','withdrawn')),
 row_version integer NOT NULL DEFAULT 1 CHECK(row_version>=1),created_at timestamptz NOT NULL DEFAULT now(),
 published_at timestamptz,published_by uuid,withdrawn_at timestamptz,withdrawn_by uuid,
 UNIQUE(id,organization_id),UNIQUE(document_id,revision_no),
 FOREIGN KEY(document_id,organization_id) REFERENCES knowledge_documents(id,organization_id),
 FOREIGN KEY(created_by,organization_id) REFERENCES users(id,organization_id),
 FOREIGN KEY(published_by,organization_id) REFERENCES users(id,organization_id),
 FOREIGN KEY(withdrawn_by,organization_id) REFERENCES users(id,organization_id),
 CHECK((status IN ('published','withdrawn'))=(published_at IS NOT NULL AND published_by IS NOT NULL)),
 CHECK((status='withdrawn')=(withdrawn_at IS NOT NULL AND withdrawn_by IS NOT NULL))
);
ALTER TABLE knowledge_documents ADD FOREIGN KEY(current_revision_id,organization_id) REFERENCES knowledge_revisions(id,organization_id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE knowledge_documents ADD FOREIGN KEY(published_revision_id,organization_id) REFERENCES knowledge_revisions(id,organization_id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE knowledge_chunks (
 id uuid PRIMARY KEY,organization_id uuid NOT NULL,revision_id uuid NOT NULL,chunk_no integer NOT NULL CHECK(chunk_no>=1),
 text_content text NOT NULL CHECK(length(text_content) BETWEEN 1 AND 1800),text_sha256 text NOT NULL CHECK(text_sha256 ~ '^[a-f0-9]{64}$'),
 heading_path jsonb NOT NULL,line_start integer NOT NULL CHECK(line_start>=1),line_end integer NOT NULL,
 char_start integer NOT NULL CHECK(char_start>=0),char_end integer NOT NULL,
 UNIQUE(id,organization_id),UNIQUE(revision_id,chunk_no),
 FOREIGN KEY(revision_id,organization_id) REFERENCES knowledge_revisions(id,organization_id),
 CHECK(line_end>=line_start AND char_end>char_start AND char_end-char_start=length(text_content))
);
CREATE TABLE knowledge_review_grants (
 user_id uuid PRIMARY KEY,organization_id uuid NOT NULL,enabled boolean NOT NULL,row_version integer NOT NULL CHECK(row_version>=1),
 changed_by uuid NOT NULL,changed_at timestamptz NOT NULL DEFAULT now(),reason text NOT NULL,
 FOREIGN KEY(user_id,organization_id) REFERENCES users(id,organization_id),
 FOREIGN KEY(changed_by,organization_id) REFERENCES users(id,organization_id)
);
CREATE TABLE knowledge_commands (
 id uuid PRIMARY KEY,organization_id uuid NOT NULL,actor_id uuid NOT NULL,
 operation text NOT NULL CHECK(operation IN ('create','revise','submit','reject','publish','withdraw','review_grant')),
 key_hash text NOT NULL,request_hash text NOT NULL,document_id uuid,revision_id uuid,reviewer_id uuid,
 created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(organization_id,actor_id,key_hash),
 FOREIGN KEY(actor_id,organization_id) REFERENCES users(id,organization_id),
 FOREIGN KEY(document_id,organization_id) REFERENCES knowledge_documents(id,organization_id),
 FOREIGN KEY(revision_id,organization_id) REFERENCES knowledge_revisions(id,organization_id),
 FOREIGN KEY(reviewer_id,organization_id) REFERENCES users(id,organization_id),
 CHECK(key_hash ~ '^[a-f0-9]{64}$' AND request_hash ~ '^[a-f0-9]{64}$')
);
CREATE TABLE knowledge_events (
 id uuid PRIMARY KEY,organization_id uuid NOT NULL,actor_id uuid NOT NULL,document_id uuid,revision_id uuid,
 action text NOT NULL,reason text NOT NULL,details jsonb NOT NULL DEFAULT '{}',created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(actor_id,organization_id) REFERENCES users(id,organization_id),
 FOREIGN KEY(document_id,organization_id) REFERENCES knowledge_documents(id,organization_id),
 FOREIGN KEY(revision_id,organization_id) REFERENCES knowledge_revisions(id,organization_id)
);
CREATE INDEX knowledge_documents_scope ON knowledge_documents(organization_id,asset_id,created_at,id);
CREATE INDEX knowledge_revisions_document ON knowledge_revisions(document_id,revision_no DESC);
CREATE TRIGGER knowledge_chunks_immutable BEFORE UPDATE OR DELETE ON knowledge_chunks FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE TRIGGER knowledge_commands_immutable BEFORE UPDATE OR DELETE ON knowledge_commands FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE TRIGGER knowledge_events_immutable BEFORE UPDATE OR DELETE ON knowledge_events FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE FUNCTION protect_knowledge_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'knowledge history immutable'; END IF;
 IF TG_OP='INSERT' THEN
   IF NEW.status<>'draft' OR NEW.row_version<>1 THEN RAISE EXCEPTION 'new knowledge must be draft'; END IF;
 ELSE
   IF ROW(NEW.id,NEW.organization_id,NEW.document_id,NEW.revision_no,NEW.created_by,NEW.original_filename,NEW.format,
     NEW.original_bytes,NEW.source_sha256,NEW.text_content,NEW.text_sha256,NEW.metadata,NEW.metadata_sha256,
     NEW.chunk_policy,NEW.chunk_count,NEW.reason,NEW.created_at)
    IS DISTINCT FROM ROW(OLD.id,OLD.organization_id,OLD.document_id,OLD.revision_no,OLD.created_by,OLD.original_filename,OLD.format,
     OLD.original_bytes,OLD.source_sha256,OLD.text_content,OLD.text_sha256,OLD.metadata,OLD.metadata_sha256,
     OLD.chunk_policy,OLD.chunk_count,OLD.reason,OLD.created_at)
   THEN RAISE EXCEPTION 'knowledge content immutable'; END IF;
   IF NEW.row_version<>OLD.row_version+1 OR NOT ((OLD.status='draft' AND NEW.status='pending_review')
     OR (OLD.status='pending_review' AND NEW.status IN ('draft','published')) OR (OLD.status='published' AND NEW.status='withdrawn'))
   THEN RAISE EXCEPTION 'invalid knowledge transition'; END IF;
   IF OLD.published_at IS NOT NULL AND ROW(NEW.published_at,NEW.published_by) IS DISTINCT FROM ROW(OLD.published_at,OLD.published_by)
   THEN RAISE EXCEPTION 'publication history immutable'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER knowledge_revision_guard BEFORE INSERT OR UPDATE OR DELETE ON knowledge_revisions FOR EACH ROW EXECUTE FUNCTION protect_knowledge_revision();
CREATE FUNCTION protect_knowledge_document() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r knowledge_revisions;
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'knowledge document history retained'; END IF;
 IF TG_OP='UPDATE' THEN
  IF NEW.row_version<>OLD.row_version+1 OR ROW(NEW.id,NEW.organization_id,NEW.owner_id,NEW.visibility,NEW.asset_id,NEW.created_at)
   IS DISTINCT FROM ROW(OLD.id,OLD.organization_id,OLD.owner_id,OLD.visibility,OLD.asset_id,OLD.created_at)
  THEN RAISE EXCEPTION 'knowledge scope immutable'; END IF;
 END IF;
 IF NEW.current_revision_id IS NOT NULL THEN
  SELECT * INTO r FROM knowledge_revisions WHERE id=NEW.current_revision_id AND organization_id=NEW.organization_id;
  IF r.document_id IS DISTINCT FROM NEW.id THEN RAISE EXCEPTION 'knowledge current binding mismatch'; END IF;
 END IF;
 IF NEW.published_revision_id IS NOT NULL THEN
  SELECT * INTO r FROM knowledge_revisions WHERE id=NEW.published_revision_id AND organization_id=NEW.organization_id;
  IF r.document_id IS DISTINCT FROM NEW.id OR r.status<>'published' THEN RAISE EXCEPTION 'knowledge published binding mismatch'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER knowledge_document_guard BEFORE INSERT OR UPDATE OR DELETE ON knowledge_documents FOR EACH ROW EXECUTE FUNCTION protect_knowledge_document();
CREATE FUNCTION protect_knowledge_review_grant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'review grants retained'; END IF;
 IF TG_OP='UPDATE' AND (ROW(NEW.user_id,NEW.organization_id) IS DISTINCT FROM ROW(OLD.user_id,OLD.organization_id) OR NEW.row_version<>OLD.row_version+1)
 THEN RAISE EXCEPTION 'invalid review grant update'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER knowledge_review_grant_guard BEFORE UPDATE OR DELETE ON knowledge_review_grants FOR EACH ROW EXECUTE FUNCTION protect_knowledge_review_grant();
''')


def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
