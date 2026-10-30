import pytest
from vibradiag.domain.measurement_errors import MeasurementError
from vibradiag.infrastructure.persistence.file_store import FileStore


@pytest.mark.parametrize('key',['../outside.csv','/absolute.csv','C:/outside.csv','..\\outside.csv','.'])
def test_storage_keys_cannot_escape_root(monkeypatch,tmp_path,key):
    monkeypatch.setenv('VIBRADIAG_DATA_ROOT',str(tmp_path/'private'))
    with pytest.raises(MeasurementError):
        FileStore().resolve(key)
