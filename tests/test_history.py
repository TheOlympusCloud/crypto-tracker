"""The full history of my own addresses, filled in across syncs (tracker/history.py)."""

import pytest
from test_core import BCH, DOGE, LTC, SATOSHI, SOL, TRX, A

from tracker import chains, history


@pytest.fixture
def evm(monkeypatch):
    monkeypatch.setattr(
        chains,
        "DEFAULT_EVM",
        {
            "ethereum": {"url": "http://eth/api", "symbol": "ETH", "llama": "ethereum"},
            "base": {"symbol": "ETH", "llama": "base", "needs_key": True},
        },
    )


def test_each_chain_has_its_sources(evm):
    assert history.sources(SATOSHI) == ["bitcoin"]
    assert history.sources(LTC) == ["litecoin"]
    assert history.sources(BCH) == ["bitcoincash"]
    assert history.sources(DOGE) == ["dogecoin"]
    # every EVM chain this server can read, each list on its own; none for a chain without key
    assert history.sources(A) == ["ethereum:txlist", "ethereum:txlistinternal", "ethereum:tokentx"]
    assert history.sources(TRX) == ["tron:native", "tron:trc20"]
    assert history.sources(SOL) == ["solana"]


def test_a_bookmark_round_trips(db):
    assert history.get(db, A, "ethereum:txlist") is None
    history.put(db, A, "ethereum:txlist", oldest="123", newest=456, oldest_ts=1000)
    history.put(db, A, "ethereum:txlist", complete=1)  # a later write keeps the rest
    mark = history.get(db, A, "ethereum:txlist")
    assert (mark["oldest"], mark["newest"], mark["oldest_ts"], mark["complete"]) == (
        "123",
        456,
        1000,
        1,
    )


def test_an_address_is_complete_only_when_every_source_is(db, evm):
    assert history.state(db, A) == {"complete": False, "oldest_ts": None}  # nothing read yet
    history.put(db, A, "ethereum:txlist", complete=1, oldest_ts=100)
    history.put(db, A, "ethereum:txlistinternal", complete=1, oldest_ts=200)
    history.put(db, A, "ethereum:tokentx", oldest_ts=300)
    # Filling in: read back to the latest of the sources still filling in.
    assert history.state(db, A) == {"complete": False, "oldest_ts": 300}
    history.put(db, A, "ethereum:tokentx", complete=1)
    assert history.state(db, A) == {"complete": True, "oldest_ts": 100}
