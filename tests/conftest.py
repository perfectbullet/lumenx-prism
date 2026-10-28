import pytest


@pytest.fixture(autouse=True)
def no_live_history(monkeypatch):
    monkeypatch.delenv('PRISM_MONGODB_URI', raising=False)
