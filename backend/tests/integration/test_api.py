from fastapi.testclient import TestClient
from vibradiag.main import app
import pytest

@pytest.fixture(autouse=True)
def enable_local_prototype(monkeypatch):
    monkeypatch.setenv('VIBRADIAG_ENABLE_PROTOTYPE','true')

client=TestClient(app)

def test_upload_persist_and_read():
    sample=client.get('/api/v1/samples/sine.csv')
    response=client.post('/api/v1/prototype/analyses',files={'file':('sample.csv',sample.content,'text/csv')},
                         data={'sampling_rate':'4096','unit':'m/s2'})
    assert response.status_code==200
    result=response.json()
    saved=client.get('/api/v1/prototype/analyses/'+result['analysis_id'])
    assert saved.status_code==200
    assert saved.json()['sha256']==result['sha256']
    assert result['peaks'][0]['hz']==128

def test_invalid_parameters_and_path():
    response=client.post('/api/v1/prototype/analyses',files={'file':('sample.csv',b'value\n0')},data={'sampling_rate':'0','unit':'m/s2'})
    assert response.status_code==422
    assert client.get('/api/v1/prototype/analyses/not-a-valid-id').status_code==404

def test_rpm_must_be_finite():
    response=client.post('/api/v1/diagnoses',content='{"analysis_id":"x","description":"x","rpm":1e999}',headers={'content-type':'application/json'})
    assert response.status_code==422
