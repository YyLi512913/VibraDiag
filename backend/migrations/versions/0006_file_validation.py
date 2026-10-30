"""Persist validated input shape without changing legacy prototype records."""
from alembic import op

revision='0006_file_validation'
down_revision='0005_artifact_history'


def upgrade():
    op.get_bind().exec_driver_sql("""
ALTER TABLE file_objects ADD COLUMN validation_summary jsonb NOT NULL DEFAULT '{}'
 CHECK(jsonb_typeof(validation_summary)='object');
CREATE FUNCTION protect_original_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.id,NEW.organization_id,NEW.storage_key,NEW.sha256,NEW.byte_size,NEW.created_by,NEW.created_at,NEW.validation_summary)
 IS DISTINCT FROM ROW(OLD.id,OLD.organization_id,OLD.storage_key,OLD.sha256,OLD.byte_size,OLD.created_by,OLD.created_at,OLD.validation_summary)
 THEN RAISE EXCEPTION 'original file identity is immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER original_identity_immutable BEFORE UPDATE ON file_objects
 FOR EACH ROW EXECUTE FUNCTION protect_original_identity();
""")


def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
