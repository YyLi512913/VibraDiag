"""Organization, identity, sessions and append-only audit."""
from pathlib import Path
from alembic import op

revision = '0001_identity_audit'
down_revision = None


def upgrade():
    sql = (Path(__file__).parents[1] / 'sql' / '0001_identity_audit.sql').read_text(encoding='utf8')
    op.get_bind().exec_driver_sql(sql)


def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
