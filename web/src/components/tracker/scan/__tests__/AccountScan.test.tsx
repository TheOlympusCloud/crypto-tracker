// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("react-i18next", async () => (await import("@/components/tracker/__tests__/i18n-mock")).i18nMock());

import { stubApi } from "@/components/tracker/lookup/__tests__/fixture";
import type { Exposure } from "@/lib/tracker/types";
import { AccountScan } from "../AccountScan";

const EVM = "0x" + "a".repeat(40);
const SOL = "vines1vzrYbzLMRdu58ou5XTby4qAqVRLmqo36NKPTg";

const exchange = {
  id: "x-exchange-exposure", severity: "high", scoreImpact: -15, confidence: "deterministic",
  params: { exchange: "Binance", count: 3, chains: "Ethereum" },
  evidence: [{ chain: "ethereum", txid: "0xfeedbeef00112233", url: "https://etherscan.io/tx/0xfeedbeef00112233" }],
  adversaryTiers: ["kyc_exchange"], temporality: "historical",
} as const;
const scan = (over: Partial<Exposure> = {}): Exposure => ({
  address: EVM, chain: "evm", chains: ["ethereum"], icon: "ethereum", score: 78, grade: "B", partial: false,
  findings: [{ ...exchange, adversaryTiers: [...exchange.adversaryTiers], evidence: [...exchange.evidence], params: { ...exchange.params } }],
  gaps: [], explorer_address: "https://etherscan.io/address/{}", ...over,
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("the account scan", () => {
  it("shows the grade, the findings and the transactions they rest on", async () => {
    window.location.hash = `#addr=${EVM}&chains=ethereum`;
    const fetch = stubApi({ [`exposure/${EVM}?chains=ethereum`]: scan() });
    render(<AccountScan />);
    expect(await screen.findByRole("heading", { level: 1, name: "Good privacy, minor concerns" })).toBeTruthy();
    expect(screen.getByTestId("score-display").dataset.grade).toBe("B");
    expect(screen.getAllByText("Dealt directly with an exchange: Binance").length).toBeGreaterThan(0);
    const tx = screen.getByRole("link", { name: /0xfeedb/ });
    expect(tx.getAttribute("href")).toBe("https://etherscan.io/tx/0xfeedbeef00112233");
    expect(screen.getByText("Read on Ethereum.")).toBeTruthy();
    expect(screen.queryByTestId("partly-checked")).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(1); // the chains were given: no question
  });

  it("shows the address's public name and the addresses it lists", async () => {
    window.location.hash = `#addr=${EVM}&chains=ethereum`;
    const named = { severity: "high", scoreImpact: -15, confidence: "deterministic", evidence: [], adversaryTiers: ["passive_observer"], temporality: "active_risk" } as const;
    const btc = "bc1qjqg9slurvjukfl92wp58y94480fvh4uc2pwa6n";
    stubApi({
      [`exposure/${EVM}?chains=ethereum`]: scan({
        score: 73,
        grade: "C",
        findings: [
          { ...named, id: "x-name-record", params: { name: "brantly.eth" }, adversaryTiers: [...named.adversaryTiers], evidence: [] },
          { ...named, id: "x-name-addresses", severity: "medium", scoreImpact: -5, params: { name: "brantly.eth", count: 1, chains: "Bitcoin", addresses: btc }, adversaryTiers: [...named.adversaryTiers], evidence: [] },
        ],
      }),
    });
    render(<AccountScan />);
    expect((await screen.findAllByText("Has a public name: brantly.eth")).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /Its name lists addresses on other chains/ }));
    expect(screen.getAllByText(`brantly.eth lists 1 address on Bitcoin: ${btc}. Anyone who looks the name up ties that address to this one.`).length).toBeGreaterThan(0);
    expect(screen.queryByText("What the findings rest on")).toBeNull(); // a name rests on no transaction
  });

  it("says partly checked, and why, when the read was partial", async () => {
    window.location.hash = `#addr=${SOL}`;
    stubApi({
      resolve: { address: SOL, chain: "solana", chains: [], hint: null, needs_key: null, icon: "solana" },
      [`exposure/${SOL}`]: scan({ address: SOL, chain: "solana", chains: ["solana"], icon: "solana", findings: [], score: 93, grade: "A+", partial: true, gaps: [{ id: "truncated" }] }),
    });
    render(<AccountScan />);
    const note = await screen.findByTestId("partly-checked");
    expect(note.textContent).toContain("Partly checked");
    expect(note.textContent).toContain("only its newest transactions were read");
  });

  // The dial shows the grade; the headline never says "excellent" for what was not checked.
  it.each([
    ["no finding", { findings: [], score: 93, grade: "A+" as const }, "No finding in what was checked"],
    ["a partial read", { partial: true, gaps: [{ id: "truncated" as const }] }, "What the scan found so far"],
  ])("gives a neutral headline for %s", async (_, over, headline) => {
    window.location.hash = `#addr=${EVM}&chains=ethereum`;
    stubApi({ [`exposure/${EVM}?chains=ethereum`]: scan(over) });
    render(<AccountScan />);
    expect(await screen.findByRole("heading", { level: 1, name: headline })).toBeTruthy();
    expect(screen.queryByText(/Excellent|Good privacy/)).toBeNull();
  });

  it("asks an EVM address for its chains, then scans those", async () => {
    window.location.hash = `#addr=${EVM}`;
    const fetch = stubApi({
      resolve: { address: EVM, chain: "evm", chains: ["ethereum", "avalanche"], hint: null, needs_key: null, icon: "evm" },
      [`exposure/${EVM}?chains=avalanche`]: scan({ chains: ["avalanche"], icon: "avalanche" }),
    });
    render(<AccountScan />);
    expect(await screen.findByRole("heading", { name: "Which chains?" })).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: /Ethereum/ })); // keep Avalanche only
    fireEvent.click(screen.getByRole("button", { name: "Scan" }));
    expect(window.location.hash).toBe(`#addr=${EVM}&chains=avalanche`);
    await waitFor(() => expect(screen.getByTestId("score-display")).toBeTruthy());
    expect(fetch.mock.calls.map(([url]) => url)).toContain(`/api/tracker/exposure/${EVM}?chains=avalanche`);
  });

  it("reports a scan that failed, and tries again", async () => {
    window.location.hash = `#addr=${EVM}&chains=ethereum`;
    let fail = true;
    stubApi({ [`exposure/${EVM}?chains=ethereum`]: () => (fail ? new Error("the indexer did not answer: HTTP 502") : scan()) });
    render(<AccountScan />);
    expect(await screen.findByText("the indexer did not answer: HTTP 502")).toBeTruthy();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("score-display")).toBeTruthy();
  });
});
