"""Organization, identity, sessions and append-only audit."""
from pathlib import Path
from alembic import op

revision = '0002_assets_files'
down_revision = '0001_identity_audit'


def upgrade():
    sql = (Path(__file__).parents[1] / 'sql' / '0002_assets_files.sql').read_text(encoding='utf8')
    op.get_bind().exec_driver_sql(sql)


def downgrade():
    raise RuntimeError('Destructive downgrade disabled; restore a verified backup')
