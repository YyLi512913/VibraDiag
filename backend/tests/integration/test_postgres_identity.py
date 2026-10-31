"""Runs against the dedicated PostgreSQL test database, never SQLite."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import runpy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from vibradiag.core.config import setting
from vibradiag.core.paths import BACKEND_ROOT
from vibradiag.infrastructure.persistence.database import engine, transaction
from vibradiag.main import app
from vibradiag.services.identity_service import (Principal, asset_allowed, authenticate, digest,
                                               measurement_allowed, password_hasher)



def sign_in(client,data,name='owner',org='org'):
    response=client.post('/api/v1/auth/login',json={'organization_id':str(data[org]),'username':name,'password':data['password']})
    assert response.status_code==200,response.text
    return response.json()['csrf_token']


def principal(data,user,roles):
    return Principal(data[user],data['other_org'] if user=='outsider' else data['org'],uuid4(),frozenset(roles),'unused')


def test_schema_and_non_superuser(database):
    with transaction() as conn:
        tables=conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version'" )).scalars().all()
        assert len(tables)==44  # Business tables; extension/checkpoint schemas remain separate.
        assert not conn.execute(text('SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname=current_user')).scalar()
        assert not conn.execute(text("SELECT has_table_privilege(current_user,'audit_events','UPDATE')")).scalar()
        assert not conn.execute(text("SELECT has_table_privilege(current_user,'alembic_version','UPDATE')")).scalar()
    with pytest.raises(DBAPIError),transaction() as conn:
        conn.execute(text('CREATE TABLE forbidden_test_table(id int)'))


def test_login_csrf_logout_and_hash_storage(database):
    with TestClient(app) as client:
        assert client.get('/api/v1/auth/me').status_code==401
        csrf=sign_in(client,database)
        assert client.get('/api/v1/auth/me').json()['roles']==['inspector']
        raw=client.cookies.get('vibradiag_session')
        with transaction() as conn:
            row=conn.execute(text('SELECT token_hash,csrf_hash FROM auth_sessions WHERE token_hash=:hash'),dict(hash=digest(raw))).one()
            assert row.token_hash!=raw and row.csrf_hash==digest(csrf)
        assert client.post('/api/v1/auth/logout').status_code==403
        assert client.post('/api/v1/auth/logout',headers={'x-csrf-token':csrf}).status_code==204
        client.cookies.set('vibradiag_session',raw,path='/api/v1')
        assert client.get('/api/v1/auth/me').status_code==401


def test_invalid_credentials_origin_and_redaction(database):
    with TestClient(app) as client:
        payload=dict(organization_id=str(database['org']),username='missing',password=database['password'])
        assert client.post('/api/v1/auth/login',json=payload).status_code==401
        payload['username']='owner';payload['password']='wrong'
        response=client.post('/api/v1/auth/login',json=payload)
        assert response.status_code==401 and 'wrong' not in response.text
        assert client.post('/api/v1/auth/login',json=payload,headers={'origin':'https://evil.example'}).status_code==403
        payload['password']='x'*1025
        response=client.post('/api/v1/auth/login',json=payload)
        assert response.status_code==422 and 'x'*30 not in response.text


def test_expiry_disable_and_role_revocation(database):
    with TestClient(app) as client:
        sign_in(client,database)
        token=client.cookies.get('vibradiag_session')
        with transaction() as conn:
            conn.execute(text('UPDATE auth_sessions SET expires_at=now()-interval \'1 second\' WHERE token_hash=:hash'),dict(hash=digest(token)))
        assert client.get('/api/v1/auth/me').status_code==401
        sign_in(client,database)
        with transaction() as conn:
            conn.execute(text("UPDATE users SET status='disabled' WHERE id=:owner"),database)
        assert client.get('/api/v1/auth/me').status_code==401
        with transaction() as conn:
            conn.execute(text("UPDATE users SET status='active' WHERE id=:owner"),database)
            conn.execute(text("DELETE FROM role_assignments WHERE user_id=:owner"),database)
        assert client.get('/api/v1/auth/me').json()['roles']==[]
        with transaction() as conn:
            conn.execute(text("INSERT INTO role_assignments(user_id,organization_id,role,granted_by) VALUES (:owner,:org,'inspector',:owner)"),database)


def test_asset_lists_two_users_admin_cross_org_and_revoke(database):
    for name,org,count in [('owner','org',1),('peer','org',0),('admin','org',0),('outsider','other_org',0)]:
        with TestClient(app) as client:
            sign_in(client,database,name,org)
            assert len(client.get('/api/v1/assets').json()['items'])==count
            response=client.get(f"/api/v1/assets/{database['asset']}/measurement-points")
            assert response.status_code==(200 if count else 404)
    with TestClient(app) as client:
        sign_in(client,database)
        with transaction() as conn:
            conn.execute(text('UPDATE asset_grants SET can_read=false,can_write=false WHERE user_id=:owner'),database)
        assert client.get('/api/v1/assets').json()['items']==[]
        assert client.get(f"/api/v1/assets/{database['asset']}/measurement-points").status_code==404
        with transaction() as conn:
            conn.execute(text('UPDATE asset_grants SET can_read=true,can_write=true WHERE user_id=:owner'),database)


def test_measurement_scope_requires_record_and_asset(database):
    owner=principal(database,'owner',{'inspector'})
    peer=principal(database,'peer',{'engineer'})
    admin=principal(database,'admin',{'admin'})
    outsider=principal(database,'outsider',{'engineer'})
    with transaction() as conn:
        assert measurement_allowed(conn,owner,database['measurement'],write=True)
        for user in (peer,admin,outsider):
            assert not measurement_allowed(conn,user,database['measurement'])
        conn.execute(text('INSERT INTO asset_grants VALUES (:org,:peer,:asset,true,true)'),database)
        assert not measurement_allowed(conn,peer,database['measurement'])
        conn.execute(text('INSERT INTO measurement_grants VALUES (:org,:measurement,:peer,true,true,:owner)'),database)
        assert measurement_allowed(conn,peer,database['measurement'],write=True)
        conn.execute(text('UPDATE asset_grants SET can_read=false,can_write=false WHERE user_id=:peer'),database)
        assert not measurement_allowed(conn,peer,database['measurement'])
        conn.execute(text('DELETE FROM measurement_grants WHERE user_id=:peer'),database)
        conn.execute(text('DELETE FROM asset_grants WHERE user_id=:peer'),database)


def test_cross_org_fk_and_scope_checks(database):
    with pytest.raises(IntegrityError),transaction() as conn:
        conn.execute(text('INSERT INTO asset_grants VALUES (:org,:outsider,:asset,true,false)'),database)
    with pytest.raises(IntegrityError),transaction() as conn:
        conn.execute(text('INSERT INTO asset_grants VALUES (:org,:peer,:asset,false,true)'),database)


@pytest.mark.parametrize('rate',['NaN','Infinity','-Infinity','0','-1'])
def test_non_finite_or_invalid_rates_rejected(database,rate):
    with pytest.raises(IntegrityError),transaction() as conn:
        conn.execute(text('''INSERT INTO measurement_revisions(id,organization_id,measurement_id,revision_no,
            sampling_rate_hz,acceleration_unit,source_type,parameter_sources,reason,created_by)
            VALUES (:new,:org,:measurement,2,CAST(:rate AS float8),'m/s2','unknown','{}','invalid',:owner)'''),
            dict(database,new=uuid4(),rate=rate))


def test_immutable_history_and_terminal_requires_artifacts(database):
    with transaction() as conn:
        from vibradiag.services.identity_service import audit
        audit(conn,database['org'],database['owner'],'test','measurement',database['measurement'])
    for query in ['UPDATE measurement_revisions SET reason=\'changed\' WHERE id=:revision',
                  'DELETE FROM audit_events WHERE organization_id=:org']:
        with pytest.raises(DBAPIError),transaction() as conn:
            conn.execute(text(query),database)
    with pytest.raises(DBAPIError),transaction() as conn:
        conn.execute(text('''INSERT INTO analysis_runs(id,organization_id,measurement_revision_id,confirmation_id,
            requested_by,status,algorithm_version,config_hash,config_snapshot,finished_at,metrics)
            VALUES (:new,:org,:revision,:confirmation,:owner,'completed','test',:hash,'{}',now(),'{}')'''),
            dict(database,new=uuid4(),hash='a'*64))


def test_current_revision_cannot_point_to_other_measurement(database):
    with pytest.raises(IntegrityError),transaction() as conn:
        conn.execute(text('''INSERT INTO measurements(id,organization_id,file_object_id,owner_id,
            original_filename,state,current_revision_id) VALUES (:new,:org,:file,:owner,'x','ready',:revision)'''),dict(database,new=uuid4()))


def test_completed_artifacts_and_result_cannot_be_rewritten(database):
    run=uuid4()
    with transaction() as conn:
        conn.execute(text('''INSERT INTO analysis_runs(id,organization_id,measurement_revision_id,confirmation_id,
            requested_by,status,algorithm_version,config_hash,config_snapshot)
            VALUES (:run,:org,:revision,:confirmation,:owner,'running','test',:hash,'{}')'''),
            dict(database,run=run,hash='b'*64))
        for kind in ('result','waveform_preview','amplitude_spectrum','manifest'):
            conn.execute(text('''INSERT INTO analysis_artifacts(id,organization_id,analysis_id,kind,storage_key,
                sha256,byte_size,format,manifest,state) VALUES (:id,:org,:run,:kind,:key,:hash,20,'json','{}','ready')'''),
                dict(id=uuid4(),org=database['org'],run=run,kind=kind,key=str(run)+'/'+kind,hash='c'*64))
        conn.execute(text("UPDATE analysis_runs SET status='completed',finished_at=now(),metrics='{}' WHERE id=:run"),dict(run=run))
    for sql in ["UPDATE analysis_runs SET metrics=jsonb_build_object('changed',1) WHERE id=:run",
                "UPDATE analysis_artifacts SET sha256=repeat('d',64) WHERE analysis_id=:run",
                'DELETE FROM analysis_artifacts WHERE analysis_id=:run']:
        with pytest.raises(DBAPIError),transaction() as conn:
            conn.execute(text(sql),dict(run=run))


def test_point_must_belong_to_selected_asset(database):
    other_asset=uuid4()
    with transaction() as conn:
        conn.execute(text("INSERT INTO assets(id,organization_id,asset_code,name,equipment_type) VALUES (:id,:org,:code,'Other','motor')"),
                     dict(id=other_asset,org=database['org'],code=str(other_asset)))
    with pytest.raises(IntegrityError),transaction() as conn:
        conn.execute(text('''INSERT INTO measurement_revisions(id,organization_id,measurement_id,revision_no,sampling_rate_hz,
            acceleration_unit,asset_id,measurement_point_id,source_type,parameter_sources,reason,created_by)
            VALUES (:id,:org,:measurement,2,1024,'m/s2',:asset,:point,'unknown','{}','invalid',:owner)'''),
            dict(database,id=uuid4(),asset=other_asset))


def test_prototype_disabled_by_default(database):
    with TestClient(app) as client:
        assert client.get('/api/v1/prototype/analyses/'+str(uuid4())).status_code==503
        assert client.post('/api/v1/diagnoses',json={}).status_code==503


def test_login_rate_limit(database):
    from vibradiag.api.v1.identity import _attempts
    _attempts.clear()
    with TestClient(app) as client:
        payload=dict(organization_id=str(database['org']),username='missing',password='wrong')
        for _ in range(20):
            assert client.post('/api/v1/auth/login',json=payload).status_code==401
        response=client.post('/api/v1/auth/login',json=payload)
        assert response.status_code==429 and response.headers['Retry-After']=='60'
    _attempts.clear()
