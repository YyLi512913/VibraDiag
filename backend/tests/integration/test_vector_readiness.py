from vibradiag.infrastructure.persistence.database import transaction
from vibradiag.infrastructure.retrieval.vector_readiness import inspect_vector


def test_vector_readiness_is_read_only_with_unprivileged_app(database):
    with transaction() as connection:
        connection.exec_driver_sql('SET TRANSACTION READ ONLY')
        result=inspect_vector(connection)
        assert not result['superuser'] and not result['create_database'] and not result['create_role']
        assert not result['public_ddl'] and result['extension_modified'] is False
        assert result['ready_for_vector_migration']==bool(result['installed_version'])
        assert bool(result['blockers']) != result['ready_for_vector_migration']
        if result['installed_version']:
            assert result['extension_schema']=='vibradiag_extensions'
            assert connection.exec_driver_sql("SELECT has_schema_privilege(current_user,'vibradiag_extensions','USAGE')").scalar_one()
            assert not connection.exec_driver_sql("SELECT has_schema_privilege(current_user,'vibradiag_extensions','CREATE')").scalar_one()
