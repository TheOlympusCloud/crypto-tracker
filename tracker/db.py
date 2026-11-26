import os
import re
import sqlite3

from embit import base58, bech32

SCHEMA = """
CREATE TABLE IF NOT EXISTS node(
  address TEXT PRIMARY KEY,
  mine INT NOT NULL DEFAULT 0,
  public INT NOT NULL DEFAULT 0,
  name TEXT NOT NULL DEFAULT '',
  synced_at INT,
  hub INT NOT NULL DEFAULT 0,
  error TEXT,
  wallet TEXT,
  cut INT NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS hdwallet(
  id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', public INT NOT NULL DEFAULT 0,
  descriptor TEXT NOT NULL, error TEXT);
CREATE TABLE IF NOT EXISTS price(
  key TEXT PRIMARY KEY, usd REAL, ts INT, symbol TEXT, confidence REAL);
CREATE TABLE IF NOT EXISTS snapshot(ts INT PRIMARY KEY, private REAL, public REAL, currency TEXT);
CREATE TABLE IF NOT EXISTS alert(
  id INTEGER PRIMARY KEY, ts INT, wallet TEXT, other TEXT, text TEXT, seen INT NOT NULL DEFAULT 0,
  kind TEXT NOT NULL DEFAULT 'link', params TEXT);
CREATE TABLE IF NOT EXISTS known_link(a TEXT, b TEXT, confidence REAL, PRIMARY KEY(a, b));
CREATE TABLE IF NOT EXISTS known_grade(wallet TEXT PRIMARY KEY, grade TEXT);
CREATE TABLE IF NOT EXISTS transfer(
  chain TEXT, txid TEXT, src TEXT, dst TEXT, asset TEXT, token TEXT, amount REAL, ts INT,
  PRIMARY KEY(chain, txid, src, dst, token));
CREATE INDEX IF NOT EXISTS transfer_src ON transfer(src);
CREATE INDEX IF NOT EXISTS transfer_dst ON transfer(dst);
CREATE TABLE IF NOT EXISTS link(
  a TEXT, b TEXT, kind TEXT, confidence REAL, evidence TEXT,
  PRIMARY KEY(a, b, kind, evidence));
CREATE TABLE IF NOT EXISTS label(
  address TEXT PRIMARY KEY, name TEXT, category TEXT, source TEXT, type TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS contract(address TEXT PRIMARY KEY, name TEXT, ts INT);
CREATE TABLE IF NOT EXISTS balance(
  address TEXT, chain TEXT, asset TEXT, token TEXT, amount REAL,
  name TEXT NOT NULL DEFAULT '', trust TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(address, chain, token));
CREATE TABLE IF NOT EXISTS sent(address TEXT, txid TEXT, PRIMARY KEY(address, txid));
CREATE TABLE IF NOT EXISTS wide(address TEXT, txid TEXT, PRIMARY KEY(address, txid));
CREATE TABLE IF NOT EXISTS swap(
  protocol TEXT, sender TEXT, recipient TEXT, tx_in TEXT, tx_out TEXT,
  chain TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(protocol, tx_in, recipient));
CREATE TABLE IF NOT EXISTS history(
  address TEXT, source TEXT, oldest TEXT, newest INT, oldest_ts INT, total INT,
  gap TEXT, gap_until INT, seen INT NOT NULL DEFAULT 0, complete INT NOT NULL DEFAULT 0,
  PRIMARY KEY(address, source));
CREATE TABLE IF NOT EXISTS watch(address TEXT PRIMARY KEY, ts INT);
CREATE TABLE IF NOT EXISTS coinjoin(chain TEXT, txid TEXT, PRIMARY KEY(chain, txid));
CREATE TABLE IF NOT EXISTS failure(source TEXT PRIMARY KEY, error TEXT, ts INT);
CREATE TABLE IF NOT EXISTS name(address TEXT, chain TEXT, name TEXT, listed TEXT,
  PRIMARY KEY(address, chain));
CREATE TABLE IF NOT EXISTS receipt(chain TEXT, txid TEXT, PRIMARY KEY(chain, txid));
CREATE TABLE IF NOT EXISTS quiet(address TEXT PRIMARY KEY, ts INT);
"""

EVM_RE = re.compile(r"0x[0-9a-fA-F]{40}")
CASHADDR_RE = re.compile(r"(bitcoincash:)?[qp][02-9ac-hj-np-z]{41}")
BECH32 = {"bc": "bitcoin", "ltc": "litecoin"}
# Version byte of a base58check address -> chain. Litecoin's old "3..." script addresses
# share Bitcoin's byte and are read as Bitcoin; its current ones start with M.
BASE58 = {0x00: "bitcoin", 0x05: "bitcoin", 0x30: "litecoin", 0x32: "litecoin",
          0x1E: "dogecoin", 0x16: "dogecoin", 0x41: "tron"}  # fmt: skip


def connect(path=None):
    db = sqlite3.connect(path or os.environ.get("CT_DB", "tracker.db"), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    if "wallet" not in [r["name"] for r in db.execute("PRAGMA table_info(node)")]:
        db.execute("ALTER TABLE node ADD COLUMN wallet TEXT")  # databases from before HD wallets
    if "error" not in [r["name"] for r in db.execute("PRAGMA table_info(hdwallet)")]:
        db.execute("ALTER TABLE hdwallet ADD COLUMN error TEXT")
    if "type" not in [r["name"] for r in db.execute("PRAGMA table_info(label)")]:
        db.execute("ALTER TABLE label ADD COLUMN type TEXT NOT NULL DEFAULT ''")
    if "kind" not in [r["name"] for r in db.execute("PRAGMA table_info(alert)")]:
        db.execute("ALTER TABLE alert ADD COLUMN kind TEXT NOT NULL DEFAULT 'link'")
        db.execute(
            "ALTER TABLE alert ADD COLUMN params TEXT"
        )  # a grade alert's, worded by the page
    if "cut" not in [r["name"] for r in db.execute("PRAGMA table_info(node)")]:
        db.execute("ALTER TABLE node ADD COLUMN cut INT NOT NULL DEFAULT 0")
    have = {r["name"] for r in db.execute("PRAGMA table_info(balance)")}
    for column in ("name", "trust", "reason"):  # databases from before the token check
        if column not in have:
            db.execute(f"ALTER TABLE balance ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    have = {r["name"] for r in db.execute("PRAGMA table_info(swap)")}
    for column in ("chain", "note"):  # databases from before bridge records
        if column not in have:
            db.execute(f"ALTER TABLE swap ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    if "symbol" not in [r["name"] for r in db.execute("PRAGMA table_info(price)")]:
        db.execute("DROP TABLE price")  # only a cache; rebuilt with the new columns
        db.executescript(SCHEMA)
    if not db.execute("SELECT 1 FROM meta WHERE key = 'solana_reread'").fetchone():
        # Solana histories read before signer links and rent refunds are read once more, from
        # the newest; the transfers stored stay.
        db.execute("DELETE FROM history WHERE source = 'solana'")
        db.execute("INSERT OR IGNORE INTO meta VALUES ('solana_reread', '1')")
        db.commit()
    return db


def parse(address):
    """(chain, canonical address) of a pasted address, or ValueError. Checksums are verified
    where the format has one, so a typo is refused instead of syncing as an empty wallet."""
    address = address.strip()
    if EVM_RE.fullmatch(address):
        return "evm", address.lower()  # shortcut: EIP-55 mixed-case checksum is not verified
    low = address.lower()
    for hrp, chain in BECH32.items():
        if low.startswith(hrp + "1") and bech32.decode(hrp, low)[0] is not None:
            return chain, low
    if CASHADDR_RE.fullmatch(low):  # shortcut: cashaddr checksum is not verified
        return "bitcoincash", "bitcoincash:" + low.removeprefix("bitcoincash:")
    try:
        raw = base58.decode_check(address)
        if len(raw) == 21 and raw[0] in BASE58:
            return BASE58[raw[0]], address
        secret = len(raw) in (33, 34) and raw[0] in (0x80, 0xB0, 0x9E)  # WIF: BTC, LTC, DOGE
    except (ValueError, KeyError, IndexError):
        secret = False
    if secret:
        raise ValueError("that is a private key: never enter one here")
    try:
        if len(base58.decode(address)) == 32:  # a Solana account is a bare 32-byte key
            return "solana", address
    except (ValueError, KeyError, IndexError):
        pass
    # The input is not repeated in the message: it may be a secret in a form not recognised.
    raise ValueError("not an address of a supported chain")


def norm(address):
    return parse(address)[1]


def chain_of(address):
    return parse(address)[0]


def valid(address):
    try:
        return norm(address) == address
    except (ValueError, AttributeError):
        return False
