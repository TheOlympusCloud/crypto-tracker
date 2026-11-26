"""Activity hours (docs/design/multichain.md, 2.2 #5): an estimate of a wallet's time zone
from the hours it sends at, made from stored data."""

from test_core import A, B, mine, transfer
from test_wallet_score import BTC1, BTC2, hd_wallet, scores, synced

from tracker import exposure, lookup
from tracker.exposure import hours

DAY = 86400


def at(*utc_hours, days=30):
    """Unix times: one a day at each of `utc_hours`, over `days` days."""
    return [d * DAY + 1_700_006_400 + h * 3600 for d in range(days) for h in utc_hours]


def test_it_needs_20_sent_transactions_and_says_how_many_it_rests_on():
    awake = at(9, 13, 18, 21, days=5)  # 20
    assert hours.estimate(awake[:19]) is None
    assert hours.estimate(awake)["count"] == 20


def test_the_quiet_hours_point_to_the_time_zone():
    # Asleep from midnight to 8:00 in UTC+2: nothing sent between 22:00 and 6:00 UTC.
    assert hours.estimate(at(*range(6, 22))) == {
        "count": 480,
        "quiet": "22:00–06:00",
        "zone": "UTC+2",
    }
    # New York in winter: asleep from 5:00 to 13:00 UTC.
    assert hours.estimate(at(*range(13, 24), 0, 1, 2, 3, 4))["zone"] == "UTC-5"
    # A short day leaves a long quiet stretch: the night is its middle, so a day from 10:00 to
    # 14:00 UTC is the middle of one from 8:00 to midnight in UTC+4.
    assert hours.estimate(at(10, 11, 12, 13))["zone"] == "UTC+4"


def test_no_night_shows_no_time_zone():
    assert hours.estimate(at(*range(24))) is None  # a bot, or people in several time zones
    assert hours.estimate(at(0, 8, 16)) is None  # every eight hours: a third of it at night
    # Busy around noon and around midnight: two quiet stretches, nothing tells which is night.
    assert hours.estimate(at(0, 1, 12, 13)) is None
    assert hours.estimate(at(5, 6, 17, 18)) is None


def sends(db, src, times, chain="bitcoin", asset="BTC"):
    for i, ts in enumerate(times):
        transfer(
            db, chain, f"{src[:6]}{i}", src, "1dice8EMZmqKvrGE4Qc9bUFf9PX3xaYDp", 0.1, ts, asset
        )


def test_a_wallets_hours_cost_their_stated_impact_counted_over_all_its_addresses(db):
    hd_wallet(db, BTC1, BTC2)
    awake = at(*range(6, 22), days=2)  # 32 transactions, 16 from each address
    sends(db, BTC1, awake[:16])
    (before,) = scores(db).values()
    sends(db, BTC2, awake[16:])
    (after,) = scores(db).values()
    (found,) = [f for f in after["by_chain"]["bitcoin"] if f["id"] == "x-activity-hours"]
    assert found["params"] == {"count": 32, "quiet": "22:00–06:00", "zone": "UTC+2"}
    assert found["scoreImpact"] == exposure.IMPACT["x-activity-hours"][1] == -2
    assert before["score"] - after["score"] == 2
    assert not any(f["id"] == "x-activity-hours" for f in before["findings"])  # 16: too few


def test_on_evm_only_transactions_the_address_signed_count(db):
    mine(db, A)
    synced(db, A)
    awake = at(*range(6, 22), days=2)
    for i, ts in enumerate(awake):
        transfer(db, "ethereum", f"0x{i}", A, B, 1.0, ts)
    assert not [f for f in scores(db)[A]["findings"] if f["id"] == "x-activity-hours"]
    db.executemany("INSERT INTO sent VALUES (?, ?)", [(A, f"0x{i}") for i in range(len(awake))])
    assert [f for f in scores(db)[A]["findings"] if f["id"] == "x-activity-hours"]


def test_a_scan_estimates_a_strangers_hours_from_its_own_read(db):
    awake = at(*range(6, 22), days=2)
    rows = [("ethereum", f"0x{i}", A, B, "ETH", "", 1.0, ts) for i, ts in enumerate(awake)]
    lookup.READS[(A, ("ethereum",))] = (0, {"transfers": rows, "sent": {r[1] for r in rows}})
    ctx = exposure.Context(db, None, A, {"chains": ["ethereum"]})
    assert list(hours.check(ctx)) == [
        ("x-activity-hours", {"count": 32, "quiet": "22:00–06:00", "zone": "UTC+2"}, [])
    ]
