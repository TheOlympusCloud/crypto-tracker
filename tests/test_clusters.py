from fastapi.testclient import TestClient

from tracker import clusters, graph

A, B, C, D = ("0x" + c * 40 for c in "abcd")
BTC = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"


def mine(db, address, public=0, synced=1):
    db.execute(
        "INSERT INTO node(address, mine, public, synced_at) VALUES (?, 1, ?, ?)",
        (address, public, synced),
    )


def link(db, a, b, kind, confidence):
    db.execute("INSERT INTO link VALUES (?, ?, ?, ?, 'x')", (a, b, kind, confidence))


def groups(db):
    return clusters.groups(db, graph.verdicts(db))


def test_two_links_of_half_or_more_make_one_group_naming_its_weakest_link(db):
    for w in (A, B, C):
        mine(db, w)
    link(db, A, B, "common-input", 0.9)
    link(db, B, C, "exchange-flow", 0.6)
    (group,) = groups(db)
    assert group["wallets"] == [A, B, C]
    assert group["public"] == [] and group["checked"] == "full"
    weakest, strongest = group["links"]
    assert (weakest["a"], weakest["b"], weakest["confidence"]) == (B, C, 0.6)
    assert weakest["kinds"] == ["exchange-flow"] and weakest["path"] == [B, C]
    assert strongest["confidence"] == 0.9
    # In a chain of three, each link alone holds the group together, and says what it parts.
    assert weakest["apart"] == [[A, B], [C]]
    assert strongest["apart"] == [[A], [B, C]]


def test_a_link_below_half_splits_the_group(db):
    for w in (A, B, C):
        mine(db, w)
    link(db, A, B, "common-input", 0.9)
    link(db, B, C, "change", 0.45)
    (group,) = groups(db)
    assert group["wallets"] == [A, B]
    assert [(k["a"], k["b"]) for k in group["links"]] == [(A, B)]


def test_a_group_with_a_public_wallet_is_marked(db):
    mine(db, A, public=1)
    mine(db, B)
    mine(db, C)
    mine(db, D)
    link(db, A, B, "common-input", 0.9)
    link(db, C, D, "common-input", 0.9)
    public, private = groups(db)  # the one an observer can name comes first
    assert public["wallets"] == [A, B] and public["public"] == [A]
    assert private["wallets"] == [C, D] and private["public"] == []


def test_a_weaker_direct_link_keeps_the_group_together(db):
    # A-C's own link (0.55) is weaker than the chain A-B-C (0.81), so the verdict shows the
    # chain; it still means neither A-B nor B-C alone holds the group together.
    for w in (A, B, C):
        mine(db, w)
    link(db, A, B, "common-input", 0.9)
    link(db, B, C, "common-input", 0.9)
    link(db, A, C, "exchange-flow", 0.55)
    (verdict,) = [v for v in graph.verdicts(db) if v["address"] == A]
    assert [k["via"] for k in verdict["links"] if k["wallet"] == C] == [[B]]
    (group,) = groups(db)
    assert [k["confidence"] for k in group["links"]] == [0.55, 0.9, 0.9]
    assert [k["apart"] for k in group["links"]] == [None, None, None]


def test_a_group_spans_chains_and_waits_for_partly_checked_wallets(db):
    mine(db, A)
    mine(db, BTC, synced=None)  # never synced: its own links may be missing
    db.execute("INSERT INTO transfer VALUES ('base', 't1', ?, ?, 'ETH', '', 1, 0)", (A, D))
    link(db, BTC, A, "bridge", 0.7)
    (group,) = groups(db)
    assert group["wallets"] == [A, BTC]
    assert group["chains"] == ["base", "bitcoin"]
    assert group["checked"] == "none"


def test_a_wallet_alone_or_an_hd_wallet_is_one_member(db):
    mine(db, A)
    for address in (BTC, "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"):
        db.execute(
            "INSERT INTO node(address, mine, synced_at, wallet) VALUES (?, 1, 1, 'hd:x')",
            (address,),
        )
    mine(db, B)
    link(db, A, BTC, "bridge", 0.7)
    (group,) = groups(db)
    assert group["wallets"] == [
        A,
        "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
    ]  # each by its first address


def test_analysis_sends_the_groups(db):
    from tracker.app import app

    mine(db, A, public=1)
    mine(db, B)
    link(db, A, B, "common-input", 0.9)
    db.commit()
    (group,) = (
        TestClient(app, base_url="http://localhost").get("/api/tracker/analysis").json()["groups"]
    )
    assert group["wallets"] == [A, B] and group["public"] == [A]
