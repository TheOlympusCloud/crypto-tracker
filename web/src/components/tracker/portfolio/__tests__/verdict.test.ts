import { describe, it, expect } from "vitest";
import { t } from "../../__tests__/i18n-mock";
import type { Coverage, GraphNode, Verdict, VerdictLink } from "@/lib/tracker/types";
import { caveats, judge } from "../verdict";
import { assetTotals } from "../totals";

const COVERAGE: Coverage = { synced: true, truncated: false, filling: null, error: null, counterparties: 0, expanded: 0, notes: [] };
const verdict = (over: Partial<Verdict> = {}, coverage: Partial<Coverage> = {}): Verdict => ({
  address: "a",
  wallet: "a",
  addresses: ["a"],
  checked: "full",
  name: "A",
  public: false,
  links: [],
  services: [],
  shared_services: [],
  ...over,
  coverage: { ...COVERAGE, ...coverage },
});
const link = (over: Partial<VerdictLink> = {}): VerdictLink => ({
  wallet: "b",
  wallet_id: "b",
  public: false,
  confidence: 0.9,
  path: ["a", "b"],
  kinds: ["transfer"],
  via: [],
  ...over,
});
const nameOf = (a: string) => `<${a}>`;

describe("judge", () => {
  it("says a wallet that was never read is not checked, and why", () => {
    expect(judge(verdict({ checked: "none" }), nameOf, t)).toEqual({ level: "unknown", headline: "Not checked yet: sync this wallet" });
    expect(judge(verdict({ checked: "none" }, { error: "timeout" }), nameOf, t)).toEqual({ level: "unknown", headline: "Not checked: the last sync failed" });
  });

  it("finds nothing only after a complete check", () => {
    expect(judge(verdict(), nameOf, t)).toEqual({ level: "ok", headline: "No link to your other wallets found" });
    expect(judge(verdict({ checked: "partial" }), nameOf, t)).toEqual({ level: "unknown", headline: "Partly checked: the last sync had errors" });
    expect(judge(verdict({ checked: "partial" }, { filling: 0 }), nameOf, t).headline).toBe("Partly checked: history still filling in");
    expect(judge(verdict({ checked: "partial" }, { filling: 1700000000 }), nameOf, t).headline).toBe(
      "Partly checked: history still filling in, back to 2023-11-14 so far",
    );
  });

  it("calls a link below even odds a weak guess", () => {
    expect(judge(verdict({ links: [link({ public: true, confidence: 0.4 })] }), nameOf, t)).toEqual({
      level: "warn",
      headline: "Weak guess: may be tied to public wallet <b> (40%)",
    });
    expect(judge(verdict({ links: [link({ confidence: 0.3 })] }), nameOf, t).headline).toBe("Weak guess: may be tied to private wallet <b> (30%)");
  });

  it("counts the links of a public wallet", () => {
    expect(judge(verdict({ public: true, links: [link()] }), nameOf, t)).toEqual({ level: "warn", headline: "Tied to 1 other wallet of yours" });
    expect(judge(verdict({ public: true, links: [link(), link({ wallet: "c", public: true })] }), nameOf, t).headline).toBe("Tied to 2 other wallets of yours");
  });

  it("puts a private wallet's link to a public one first, as exposed", () => {
    const links = [link(), link({ wallet: "c", public: true, confidence: 0.8 })];
    expect(judge(verdict({ links }), nameOf, t)).toEqual({ level: "bad", headline: "Linked to public wallet <c> (80%)" });
    expect(judge(verdict({ links: [link()] }), nameOf, t)).toEqual({ level: "warn", headline: "Linked to private wallet <b> (90%)" });
  });
});

describe("caveats", () => {
  const ALWAYS = [
    "Paths longer than 4 hops are not searched.",
    "Swaps outside THORChain and Maya, chains that are not read, unlabelled exchange wallets and anything off-chain (same IP address, same exchange account) are invisible to this check.",
  ];

  it("always names the search depth and what is invisible", () => {
    expect(caveats(verdict(), nameOf, 4, t)).toEqual(ALWAYS);
    expect(caveats(verdict(), nameOf, 1, t)[0]).toBe("Paths longer than 1 hop are not searched.");
  });

  it("lists every gap of the check, then the server's notes", () => {
    const v = verdict(
      {
        shared_services: [{ wallet: "c", services: ["Binance", "Kraken"] }],
        services: [
          { name: "Binance", category: "cex", guess: false },
          { name: "Kraken", category: "cex", guess: false },
          { name: "Tornado", category: "mixer", guess: false },
        ],
      },
      { error: "rate limited", truncated: true, counterparties: 5, expanded: 2, notes: ["Not read on base."] },
    );
    expect(caveats(v, nameOf, 4, t)).toEqual([
      "The last sync of this wallet had errors (rate limited), so its history may be incomplete.",
      "This wallet is very active and only its newest transactions are loaded. Older links are not seen.",
      "3 of its 5 unlabelled counterparties have not been expanded. A link that runs through two or more strangers can hide there: sync deeper, or expand them in the graph.",
      "Both this wallet and <c> used Binance, Kraken. The chain does not link them, but that service can.",
      "It dealt directly with Binance, Kraken. If that account is in your name, the exchange knows this wallet is yours: consider marking it public.",
      "It dealt with a mixer. Funds past a mixer cannot be followed here.",
      ALWAYS[0],
      "Not read on base.",
      ALWAYS[1],
    ]);
  });

  it("says a filling history instead of a truncated one, one counterparty, and no exchange advice for a public wallet", () => {
    const v = verdict({ public: true, services: [{ name: "Binance", category: "cex", guess: false }] }, { filling: 0, truncated: true, counterparties: 1, expanded: 0 });
    expect(caveats(v, nameOf, 4, t)).toEqual([
      "Links in its older transactions are not seen yet: every sync reads further back until its history is complete.",
      "1 of its 1 unlabelled counterparty has not been expanded. A link that runs through two or more strangers can hide there: sync deeper, or expand it in the graph.",
      ...ALWAYS,
    ]);
  });
});

describe("assetTotals", () => {
  const node = (address: string, pub: boolean, balances: GraphNode["balances"]): GraphNode => ({ address, mine: 1, public: pub ? 1 : 0, name: address, balances });
  const scam = { level: "scam" as const, reason: "named like USDC" };

  it("sums private and public per asset, keeps fakes apart and after the rest", () => {
    const { rows, fakes } = assetTotals([
      node("a", false, [
        { chain: "ethereum", asset: "USDC", amount: 10, token: "0xreal" },
        { chain: "ethereum", asset: "USDC", amount: 5000, token: "0xfake", trust: scam },
      ]),
      node("b", true, [
        { chain: "polygon", asset: "USDC", amount: 5, token: "0xreal2" },
        { chain: "bitcoin", asset: "BTC", amount: 1, token: "" },
      ]),
      node("c", false, undefined),
    ]);
    expect(rows.map((r) => [r.asset, r.private, r.public, [...r.chains]])).toEqual([
      ["BTC", 0, 1, ["bitcoin"]],
      ["USDC", 10, 5, ["ethereum", "polygon"]],
    ]);
    expect(fakes.map((r) => [r.asset, r.private, r.public, r.trust?.level])).toEqual([["USDC", 5000, 0, "scam"]]);
  });
});
