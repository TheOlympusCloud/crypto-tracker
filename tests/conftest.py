import pytest

from tracker import db as database


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CT_DB", str(tmp_path / "t.db"))
    db = database.connect()
    yield db
    db.close()
