"""Shared, isolated real-PostgreSQL fixture (one dataset per test module)."""
import runpy
from uuid import uuid4
import pytest
from sqlalchemy import text
from vibradiag.core.config import setting
from vibradiag.core.paths import BACKEND_ROOT
from vibradiag.infrastructure.persistence.database import engine, transaction
from vibradiag.services.identity_service import password_hasher

@pytest.fixture(scope='module')
def database():
    app_url=setting('VIBRADIAG_TEST_DATABASE_URL')
    migration_url=setting('VIBRADIAG_TEST_MIGRATION_DATABASE_URL')
    if not app_url or not migration_url:
        pytest.skip('Dedicated PostgreSQL test URLs are not configured')
    from sqlalchemy.engine import make_url
    if make_url(app_url).database==make_url(setting('VIBRADIAG_DATABASE_URL')).database:
        pytest.fail('Test database must differ from development database')
    monkeypatch=pytest.MonkeyPatch()
    monkeypatch.setenv('VIBRADIAG_DATABASE_URL',app_url)
    monkeypatch.setenv('VIBRADIAG_MIGRATION_DATABASE_URL',migration_url)
    monkeypatch.setenv('VIBRADIAG_COOKIE_SECURE','false')
    monkeypatch.setenv('VIBRADIAG_ENABLE_PROTOTYPE','false')
    engine.cache_clear()
    migrate=runpy.run_path(str(BACKEND_ROOT/'scripts/migrate.py'))['migrate']
    migrate();migrate()  # No duplicate schema or data after repeated upgrades.
    data=dict(org=uuid4(),other_org=uuid4(),owner=uuid4(),peer=uuid4(),admin=uuid4(),outsider=uuid4(),
              asset=uuid4(),point=uuid4(),file=uuid4(),measurement=uuid4(),revision=uuid4(),confirmation=uuid4())
    data['password']='Test-only random password '+str(uuid4())
    with transaction() as conn:
        for org in (data['org'],data['other_org']):
            conn.execute(text('INSERT INTO organizations(id,name) VALUES (:id,:name)'),dict(id=org,name=str(org)))
        for user,org,name,role in [('owner','org','owner','inspector'),('peer','org','peer','engineer'),
                                   ('admin','org','admin','admin'),('outsider','other_org','outsider','engineer')]:
            conn.execute(text('INSERT INTO users(id,organization_id,username_normalized,password_hash) VALUES (:id,:org,:name,:hash)'),
                dict(id=data[user],org=data[org],name=name,hash=password_hasher.hash(data['password'])))
            conn.execute(text('INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:id,:org,:role,:id)'),
                dict(id=data[user],org=data[org],role=role))
        conn.execute(text("INSERT INTO assets(id,organization_id,asset_code,name,equipment_type) VALUES (:asset,:org,'A-1','Test motor','motor')"),data)
        conn.execute(text("INSERT INTO measurement_points(id,organization_id,asset_id,point_code,name) VALUES (:point,:org,:asset,'P-1','Drive end')"),data)
        conn.execute(text("INSERT INTO asset_grants VALUES (:org,:owner,:asset,true,true)"),data)
        conn.execute(text("INSERT INTO file_objects(id,organization_id,storage_key,sha256,byte_size,state,created_by) VALUES (:file,:org,:key,:hash,20,'ready',:owner)"),
                     dict(data,key=str(data['file']),hash='a'*64))
        conn.execute(text("INSERT INTO measurements(id,organization_id,file_object_id,owner_id,original_filename,state) VALUES (:measurement,:org,:file,:owner,'test.csv','ready')"),data)
        conn.execute(text("""INSERT INTO measurement_revisions(id,organization_id,measurement_id,revision_no,sampling_rate_hz,
            acceleration_unit,asset_id,measurement_point_id,source_type,parameter_sources,reason,created_by)
            VALUES (:revision,:org,:measurement,1,1024,'m/s2',:asset,:point,'synthetic','{}','test',:owner)"""),data)
        conn.execute(text('UPDATE measurements SET current_revision_id=:revision WHERE id=:measurement'),data)
        conn.execute(text('INSERT INTO input_confirmations(id,organization_id,revision_id,actor_id) VALUES (:confirmation,:org,:revision,:owner)'),data)
    yield data
    engine().dispose();engine.cache_clear();monkeypatch.undo()

