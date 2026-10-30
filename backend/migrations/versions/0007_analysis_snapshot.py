"""Prevent rewriting accepted input/configuration identities while running."""
from alembic import op
revision='0007_analysis_snapshot'
down_revision='0006_file_validation'


def upgrade():
    op.get_bind().exec_driver_sql('''
CREATE FUNCTION protect_analysis_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.id,NEW.organization_id,NEW.measurement_revision_id,NEW.confirmation_id,NEW.requested_by,
        NEW.retry_of,NEW.algorithm_version,NEW.config_hash,NEW.config_snapshot,NEW.started_at)
 IS DISTINCT FROM ROW(OLD.id,OLD.organization_id,OLD.measurement_revision_id,OLD.confirmation_id,OLD.requested_by,
        OLD.retry_of,OLD.algorithm_version,OLD.config_hash,OLD.config_snapshot,OLD.started_at)
 THEN RAISE EXCEPTION 'accepted analysis snapshot is immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER analysis_snapshot_immutable BEFORE UPDATE ON analysis_runs
 FOR EACH ROW EXECUTE FUNCTION protect_analysis_snapshot();
''')


def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
