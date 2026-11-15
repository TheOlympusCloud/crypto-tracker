import os
import re
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS node(
  address TEXT PRIMARY KEY,
  mine INT NOT NULL DEFAULT 0,
  public INT NOT NULL DEFAULT 0,
  name TEXT NOT NULL DEFAULT '',
  synced_at INT,
  hub INT NOT NULL DEFAULT 0,
  error TEXT);
CREATE TABLE IF NOT EXISTS transfer(
  chain TEXT, txid TEXT, src TEXT, dst TEXT, asset TEXT, token TEXT, amount REAL, ts INT,
  PRIMARY KEY(chain, txid, src, dst, token));
CREATE INDEX IF NOT EXISTS transfer_src ON transfer(src);
CREATE INDEX IF NOT EXISTS transfer_dst ON transfer(dst);
CREATE TABLE IF NOT EXISTS link(
  a TEXT, b TEXT, kind TEXT, confidence REAL, evidence TEXT,
  PRIMARY KEY(a, b, kind, evidence));
CREATE TABLE IF NOT EXISTS label(address TEXT PRIMARY KEY, name TEXT, category TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS balance(
  address TEXT, chain TEXT, asset TEXT, token TEXT, amount REAL,
  PRIMARY KEY(address, chain, token));
"""

EVM_RE = re.compile(r"0x[0-9a-fA-F]{40}")
BTC_RE = re.compile(
    r"(bc1[02-9ac-hj-np-z]{11,71}|BC1[02-9AC-HJ-NP-Z]{11,71}|[13][1-9A-HJ-NP-Za-km-z]{25,34})"
)


def connect(path=None):
    db = sqlite3.connect(path or os.environ.get("CT_DB", "tracker.db"), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    return db


def norm(address):
    """Canonical form of an address, or ValueError. EVM and bech32 are case-insensitive."""
    address = address.strip()
    if EVM_RE.fullmatch(address):
        return address.lower()
    if BTC_RE.fullmatch(address):
        return address.lower() if address[:3].lower() == "bc1" else address
    # shortcut: format check only, no checksum; a typo'd address just syncs as empty.
    raise ValueError(f"not a Bitcoin or EVM address: {address[:80]}")


def family(address):
    return "evm" if address.startswith("0x") else "btc"
