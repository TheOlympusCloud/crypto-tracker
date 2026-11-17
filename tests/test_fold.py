"""Folding busy addresses in the graph (tracker/static/fold.js), run through Node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

FOLD = (Path(__file__).parent.parent / "tracker" / "static" / "fold.js").as_uri()
ME, CEX, MIXER, S1, S2, S3, T = "me", "cex", "mixer", "s1", "s2", "s3", "t"


def fold(graph, folded, keep=(), min_conf=0):
    if not shutil.which("node"):
        pytest.skip("Node is not installed")
    script = (
        f"import {{ fold }} from {json.dumps(FOLD)};"
        "const [g, f, k, m] = JSON.parse(process.argv[1]);"
        "console.log(JSON.stringify(fold(g, new Set(f), k, m)));"
    )
    args = json.dumps([graph, list(folded), list(keep), min_conf])
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script, args], capture_output=True, check=False
    )
    assert out.returncode == 0, out.stderr.decode()
    return json.loads(out.stdout)


def edge(src, dst):
    return {"src": src, "dst": dst}


# My wallet, an exchange with strangers that only deal with it (s1, s2), one that also paid
# me (s3), a stranger who only dealt with me (t), and a stranger guessed to share an owner
# with s2 through a mixer.
GRAPH = {
    "nodes": [{"address": a, "mine": int(a == ME)} for a in (ME, CEX, MIXER, S1, S2, S3, T)],
    "edges": [edge(S1, CEX), edge(CEX, S2), edge(S3, CEX), edge(S3, ME), edge(T, ME)],
    "links": [{"a": S2, "b": MIXER, "kind": "common-input", "confidence": 0.9}],
}


def shown(graph):
    return {n["address"]: n.get("folded") for n in graph["nodes"]}


def test_nothing_folded_changes_nothing():
    assert fold(GRAPH, []) == GRAPH


def test_a_folded_address_hides_the_strangers_that_only_deal_with_it():
    g = fold(GRAPH, [CEX])
    # s1 only dealt with the exchange; s2 is also tied to the mixer; s3 also paid me.
    assert shown(g) == {ME: None, CEX: 1, MIXER: None, S2: None, S3: None, T: None}
    assert edge(S1, CEX) not in g["edges"] and edge(CEX, S2) in g["edges"]


def test_strangers_between_folded_addresses_go_and_are_counted_on_both():
    g = fold(GRAPH, [CEX, MIXER])
    assert shown(g) == {ME: None, CEX: 2, MIXER: 1, S3: None, T: None}
    assert g["links"] == [] and all(S2 not in (e["src"], e["dst"]) for e in g["edges"])


def test_my_wallets_and_a_shown_path_are_never_hidden():
    g = fold(GRAPH, [ME])  # my own busy wallet folds too, but is never hidden itself
    assert shown(g) == {ME: 1, CEX: None, MIXER: None, S1: None, S2: None, S3: None}
    assert S1 in shown(fold(GRAPH, [CEX], keep=[S1, CEX]))  # a highlighted path stays whole


def test_only_what_is_drawn_ties_a_stranger_to_an_address():
    # The slider hides s2's guess (0.9) to the mixer: s2 deals with the exchange alone.
    assert shown(fold(GRAPH, [CEX], min_conf=0.95)) == {
        ME: None,
        CEX: 2,
        MIXER: None,
        S3: None,
        T: None,
    }
    # Sending to itself does not tie an address to anything.
    looped = {**GRAPH, "edges": [*GRAPH["edges"], edge(S1, S1)]}
    assert S1 not in shown(fold(looped, [CEX]))
