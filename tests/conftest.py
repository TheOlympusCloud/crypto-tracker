from collections import OrderedDict

import pytest

from tracker import db as database
from tracker import lookup


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CT_DB", str(tmp_path / "t.db"))
    db = database.connect()
    yield db
    db.close()


@pytest.fixture(autouse=True)
def fresh_reads(monkeypatch):
    """Reads kept in memory (lookup.READS) never carry over from one test to the next."""
    monkeypatch.setattr(lookup, "READS", OrderedDict())
