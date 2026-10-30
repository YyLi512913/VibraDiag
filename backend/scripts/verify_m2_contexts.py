"""Read-only schema/evidence receipt; never stores credentials or business rows."""
from hashlib import sha256
import json
import platform
from xml.etree import ElementTree
from sqlalchemy import text
from vibradiag.core.paths import PROJECT_ROOT
from vibradiag.infrastructure.persistence.database import transaction


def main():
    with transaction() as connection:
        # The application role deliberately has no access to alembic_version.
        tables = connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version' ORDER BY tablename")).scalars().all()
        version = connection.execute(text('SHOW server_version')).scalar_one()
        triggers = connection.execute(text("""SELECT trigger_name FROM information_schema.triggers
            WHERE event_object_schema='public' AND event_object_table IN
            ('bearing_revisions','diagnostic_contexts','asset_components')""")).scalars().all()
    expected = {'asset_components','bearing_revisions','diagnostic_contexts'}
    assert len(tables) == 20 and expected.issubset(tables)
    assert {'bearing_revision_immutable','diagnostic_context_immutable','component_identity_immutable'}.issubset(triggers)
    tests = ElementTree.parse(PROJECT_ROOT/'experiments/m2-t02b1-tests.xml').getroot().find('testsuite')
    assert int(tests.attrib['failures']) == int(tests.attrib['errors']) == int(tests.attrib['skipped']) == 0
    paths = [
        'backend/migrations/versions/0009_diagnostic_contexts.py',
        'backend/src/vibradiag/schemas/diagnostic_contexts.py',
        'backend/src/vibradiag/services/diagnostic_context_service.py',
        'backend/src/vibradiag/services/compute_capacity.py',
        'backend/src/vibradiag/services/persistent_analysis_service.py',
        'backend/scripts/manage_diagnostics.py', 'backend/scripts/verify_m2_contexts.py',
        'backend/tests/integration/test_diagnostic_contexts.py',
        'backend/tests/integration/test_migration_rebuild.py',
        'backend/tests/integration/test_postgres_identity.py',
        'backend/requirements.lock.txt', 'experiments/m2-t02b1-tests.xml',
    ]
    receipt = dict(scope='M2-T02b1 target/context persistence; no tool-run API or qualification',
        python=platform.python_version(), postgresql=version, business_table_count=len(tables),
        added_tables=sorted(expected), immutable_triggers=sorted(set(triggers)),
        full_backend_tests={key:tests.attrib[key] for key in ('tests','failures','errors','skipped','time')},
        source_sha256={path:sha256((PROJECT_ROOT/path).read_bytes()).hexdigest() for path in paths})
    output = PROJECT_ROOT/'experiments/m2-t02b1-contexts.json'
    output.write_text(json.dumps(receipt, indent=2, ensure_ascii=False)+'\n', encoding='utf8')
    print(json.dumps({key:value for key,value in receipt.items() if key != 'source_sha256'}))


if __name__ == '__main__':
    main()
