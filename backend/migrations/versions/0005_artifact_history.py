"""Protect completed artifact metadata without rewriting an applied migration."""
from alembic import op

revision='0005_artifact_history'
down_revision='0004_analysis'


def upgrade():
    op.get_bind().exec_driver_sql('''
CREATE FUNCTION reject_completed_artifact_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM analysis_runs WHERE id=OLD.analysis_id AND status='completed')
 THEN RAISE EXCEPTION 'completed artifact is immutable'; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER completed_artifact_immutable BEFORE UPDATE OR DELETE ON analysis_artifacts
 FOR EACH ROW EXECUTE FUNCTION reject_completed_artifact_mutation();
''')


def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
