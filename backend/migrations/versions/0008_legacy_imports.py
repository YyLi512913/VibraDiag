"""Explicit legacy import ledger, separate from accepted analysis results."""
from alembic import op
revision='0008_legacy_imports'
down_revision='0007_analysis_snapshot'


def upgrade():
    op.get_bind().exec_driver_sql('''
CREATE TABLE legacy_imports (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL, actor_id uuid NOT NULL,
 batch_id text NOT NULL CHECK(length(batch_id) BETWEEN 1 AND 128),
 source_scope text NOT NULL CHECK(source_scope ~ '^[a-f0-9]{64}$'),
 legacy_id text NOT NULL CHECK(length(legacy_id) BETWEEN 1 AND 128),
 raw_sha256 text NOT NULL CHECK(raw_sha256 ~ '^[a-f0-9]{64}$'),
 metadata_sha256 text NOT NULL CHECK(metadata_sha256 ~ '^[a-f0-9]{64}$'),
 measurement_id uuid NOT NULL, imported_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(organization_id,source_scope,legacy_id),
 FOREIGN KEY(actor_id,organization_id) REFERENCES users(id,organization_id),
 FOREIGN KEY(measurement_id,organization_id) REFERENCES measurements(id,organization_id)
);
CREATE TRIGGER legacy_import_immutable BEFORE UPDATE OR DELETE ON legacy_imports
 FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
''')


def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
