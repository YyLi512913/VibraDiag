"""Legacy fault-injection suite runs inline only in the dedicated test database.
Subprocess integration cases explicitly override this fixture's environment.
"""
import pytest


@pytest.fixture(autouse=True)
def numeric_test_backend(monkeypatch):
    monkeypatch.setenv('VIBRADIAG_NUMERIC_BACKEND','inline_test')
