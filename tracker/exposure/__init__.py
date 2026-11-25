"""The privacy scan of an account-chain address (EVM, Solana, Tron): am-i-exposed's findings
and score, made on the server, which reads these chains and holds the labels.

Each check is a module of its own and yields (finding id, params, evidence) from the scan's
context; the impact table below turns those into am-i-exposed's `Finding`. The server sends
ids and parameters, never sentences: the page words them (the `tracker-exposure` namespace).
Like a lookup, a scan stores nothing about the address (lookup.py).
"""

import re
from urllib.parse import quote

import httpx

from .. import chains, lookup
from .. import db as database
from . import deposit, exchange, flagged, funder, multichain, names

# id -> (severity, scoreImpact, confidence, adversaryTiers, temporality). A first proposal, to
# be tuned on real wallets (docs/design/multichain.md, 6.1).
IMPACT = {
    "x-deposit-address-shared": (
        "high",
        -10,
        "high",
        ["passive_observer", "kyc_exchange"],
        "historical",
    ),
    "x-address-reuse": ("medium", -10, "deterministic", ["passive_observer"], "historical"),
    "x-exchange-exposure": ("high", -15, "deterministic", ["kyc_exchange"], "historical"),
    "x-flagged-address": ("critical", -20, "deterministic", ["kyc_exchange"], "active_risk"),
    "x-linked-public": ("critical", -70, "deterministic", ["passive_observer"], "historical"),
    "x-flagged-counterparty": ("high", -15, "deterministic", ["kyc_exchange"], "historical"),
    "x-funder-exchange": ("medium", -5, "deterministic", ["kyc_exchange"], "historical"),
    "x-funder-hub": ("low", 0, "deterministic", ["passive_observer"], "historical"),
    "x-funder-mine": ("medium", -10, "deterministic", ["passive_observer"], "historical"),
    "x-funder-shared": ("medium", -10, "deterministic", ["passive_observer"], "historical"),
    "x-mixer-counterparty": ("medium", -10, "deterministic", ["kyc_exchange"], "historical"),
    "x-multi-chain": ("low", -2, "deterministic", ["passive_observer"], "ongoing_pattern"),
    "x-name-addresses": ("medium", -5, "deterministic", ["passive_observer"], "active_risk"),
    "x-name-record": ("high", -15, "deterministic", ["passive_observer"], "active_risk"),
}
# Checks per chain family; "any" runs on every one.
CHECKS = {
    "any": [deposit.check, exchange.check, flagged.check, funder.check],
    "evm": [multichain.check, names.check],
    "solana": [names.check],
    "tron": [],
}
FAMILIES = ("evm", "solana", "tron")
BUDGET = 60  # calls the checks of one scan may make beyond the address's own read
EVIDENCE = 5  # transactions shown per finding

# am-i-exposed's address score (web/src/lib/scoring/score.ts), ported.
BASE = 93
GRADES = ((90, "A+"), (75, "B"), (50, "C"), (25, "D"))
SEVERITY = {"critical": 0, "high": 1, "medium": 2, "low": 3, "good": 4}


def score(findings, partial=False):
    """ScoringResult: base 93 plus the impacts, clamped to 0..100; the findings worst first,
    then by the size of their impact."""
    value = max(0, min(100, BASE + sum(f["scoreImpact"] for f in findings)))
    grade = next((g for floor, g in GRADES if value >= floor), "F")
    order = sorted(findings, key=lambda f: (SEVERITY.get(f["severity"], 5), -abs(f["scoreImpact"])))
    return {"score": value, "grade": grade, "findings": order, "partial": partial}


class Spent(Exception):
    """The scan's call budget is used up."""


class Context:
    """What a check reads: the address's lookup view (every counterparty, labelled), and a
    client whose calls count against the scan's budget."""

    def __init__(self, db, client, address, view):
        self.db, self.client, self.address, self.view = db, client, address, view
        self.chain = database.chain_of(address)
        self.left = BUDGET

    def _spend(self):
        if self.left <= 0:
            raise Spent
        self.left -= 1

    def get(self, url, params=None):
        self._spend()
        return chains.get(self.client, url, params)

    def rpc(self, method, *params, url=None):
        self._spend()
        return chains._rpc(self.client, method, *params, url=url)

    def labelled(self, category):
        """Counterparties labelled `category` by a source that states it (not a guess)."""
        return [
            p
            for p in self.view["connections"]
            if p.get("category") == category and not p.get("label_guess") and not p.get("mine")
        ]

    @staticmethod
    def names(parties):
        """Their names, once per entity: "Binance 14" and "Binance #7" are Binance."""
        return ", ".join(sorted({re.sub(r"[ #:]+\d+$", "", p["label"]) for p in parties}))

    @staticmethod
    def evidence(parties):
        return [e for p in parties for e in p.get("evidence", [])][:EVIDENCE]


def finding(id, params, evidence):
    severity, impact, confidence, tiers, temporality = IMPACT[id]
    tx = lambda c: chains.explorer(c)["explorer_tx"]
    return {
        "id": id,
        "severity": severity,
        "scoreImpact": impact,
        "confidence": confidence,
        "params": params,
        "evidence": [
            {**e, "url": tx(e["chain"]).format(quote(e["txid"], safe=""))} for e in evidence
        ],
        "adversaryTiers": tiers,
        "temporality": temporality,
    }


def checked(ctx, checks, gaps):
    """The findings of `checks` on `ctx`; a check that fails or runs out of calls adds a gap."""
    found = []
    for check in checks:
        # A check module's `check` goes by the module's name: "exchange", "flagged".
        name = check.__module__.rsplit(".", 1)[-1] if check.__name__ == "check" else check.__name__
        try:
            for f in check(ctx):  # what a check found before it failed is kept
                found.append(finding(*f))
        except Spent:
            gaps.append({"id": "skipped", "params": {"check": name}})
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError, TypeError, IndexError) as e:
            gaps.append({"id": "error", "params": {"error": f"{name}: {chains.describe(e)}"}})
    return found


def run(db, client, address, evm=()):
    """Scan `address` (canonical, an account chain's) on the EVM chains `evm` (empty: all).
    `gaps` say why a result is partial: an indexer error, a read cut to the newest
    transactions, or a check that ran out of calls."""
    view = lookup.view(db, client, address, evm, cached=True, listed=None)
    ctx = Context(db, client, address, view)
    gaps = [{"id": "error", "params": {"error": e}} for e in view["errors"]]
    if view["truncated"]:
        gaps.append({"id": "truncated"})
    found = checked(ctx, CHECKS["any"] + CHECKS[ctx.chain], gaps)
    return {
        "address": address,
        "chain": ctx.chain,
        "chains": view["chains"],
        "icon": view["icon"],
        **score(found, partial=bool(gaps)),
        "gaps": gaps,
        "explorer_address": view["explorer_address"],
    }
