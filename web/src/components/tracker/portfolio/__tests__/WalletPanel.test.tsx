// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";

vi.mock("react-i18next", async () => (await import("../../__tests__/i18n-mock")).i18nMock());
vi.mock("@/lib/tracker/api", () => ({ api: vi.fn(async () => ({})) }));

import { api } from "@/lib/tracker/api";
import { ANALYSIS, GRAPH, renderTracker } from "../../__tests__/harness";
import type { GraphNode } from "@/lib/tracker/types";
import { WalletPanel } from "../WalletPanel";

afterEach(() => {
  cleanup();
  vi.mocked(api).mockClear();
});

const COLD = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq";
const PUBLIC = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh";
const HOT = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045";
const SAVINGS = "0xab5801a7d398351b8be11c439e05c5b3259aec9b";
const PHONE = "vines1vzrYbzLMRdu58ou5XTby4qAqVRLmqo36NKPTg";
const button = (name: string) => screen.getByRole("button", { name });

describe("WalletPanel", () => {
  it("shows the wallet: name, badge, value, address, history and amounts", () => {
    renderTracker(<WalletPanel address={COLD} />);
    expect(screen.getByRole("heading", { level: 2, name: "Cold storage" })).toBeTruthy();
    expect(screen.getByText("private")).toBeTruthy();
    expect(screen.getByText("$47,530")).toBeTruthy();
    expect(screen.getByText(COLD)).toBeTruthy();
    expect(screen.getByRole("img", { name: "Bitcoin" })).toBeTruthy();
    expect(screen.getByText("History complete")).toBeTruthy();
    expect(screen.getByText("BTC · bitcoin")).toBeTruthy();
  });

  it("acts on the wallet from its buttons", () => {
    const { tracker } = renderTracker(<WalletPanel address={COLD} />);
    fireEvent.click(button("Show in graph"));
    expect(tracker.showPath).toHaveBeenCalledWith([], "", COLD);
    fireEvent.click(button("Make public"));
    expect(tracker.act).toHaveBeenCalled();
    expect(api).toHaveBeenCalledWith("wallets", { address: COLD, name: "Cold storage", public: true });
    fireEvent.click(button("Re-sync"));
    expect(tracker.startSync).toHaveBeenCalledWith([COLD]);
    expect(screen.getByRole("link", { name: "Privacy score" }).getAttribute("href")).toBe(`/#addr=${encodeURIComponent(COLD)}`);
  });

  it("offers to make a public wallet private, and the tracker's privacy scan off Bitcoin", () => {
    renderTracker(<WalletPanel address={PUBLIC} />);
    fireEvent.click(button("Make private"));
    expect(api).toHaveBeenCalledWith("wallets", { address: PUBLIC, name: "Exchange withdrawals", public: false });
    cleanup();
    renderTracker(<WalletPanel address={PHONE} />);
    expect(screen.getByRole("link", { name: "Privacy score" }).getAttribute("href")).toBe(`/tracker/scan/#addr=${PHONE}`);
  });

  it("renames the wallet, then closes the form", async () => {
    const { tracker } = renderTracker(<WalletPanel address={COLD} />);
    const rename = button("Rename");
    expect(rename.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(rename);
    expect(rename.getAttribute("aria-expanded")).toBe("true");
    const input = screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement;
    expect(input.value).toBe("Cold storage");
    fireEvent.change(input, { target: { value: "Deep freeze" } });
    fireEvent.click(button("Save name"));
    expect(api).toHaveBeenCalledWith("wallets", { address: COLD, name: "Deep freeze", public: false });
    await waitFor(() => expect(screen.queryByRole("textbox")).toBeNull());
    expect(tracker.refresh).toHaveBeenCalled();
  });

  it("keeps the form open and reports a failed rename", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("name taken"));
    const { tracker } = renderTracker(<WalletPanel address={COLD} />);
    fireEvent.click(button("Rename"));
    fireEvent.click(button("Save name"));
    await waitFor(() => expect(tracker.fail).toHaveBeenCalledWith(new Error("name taken")));
    expect(screen.getByRole("textbox")).toBeTruthy();
  });

  it("says whether it can be tied to the other wallets, with evidence that opens the path", () => {
    const { tracker } = renderTracker(<WalletPanel address={COLD} />);
    expect(screen.getByRole("heading", { name: "Can it be tied to your other wallets?" })).toBeTruthy();
    expect(screen.getByText("Linked to public wallet Exchange withdrawals (70%)")).toBeTruthy();
    const row = screen.getByRole("button", { name: /Exchange withdrawals \(public\)/ });
    expect(row.textContent).toContain("2 hops, transfer");
    expect(within(row).getByTitle("70% confidence").textContent).toBe("70% confidence");
    fireEvent.click(row);
    const link = ANALYSIS.verdicts.find((v) => v.address === COLD)!.links[0]!;
    expect(tracker.showPath).toHaveBeenCalledWith(link.path, "Cold storage ↔ Exchange withdrawals", COLD);
    expect(screen.getByRole("heading", { name: "What this check may still have missed" })).toBeTruthy();
  });

  it("shows the wallet's privacy grade and its findings by chain", () => {
    renderTracker(<WalletPanel address={COLD} />);
    expect(screen.getByRole("heading", { name: "Privacy grade" })).toBeTruthy();
    expect(screen.getByText("D")).toBeTruthy();
    expect(screen.getByText("34 of 100")).toBeTruthy();
    // A Bitcoin wallet's grade leaves out the deep scan of its addresses, and says so.
    expect(screen.getByText("Wallet checks; addresses not deep-scanned")).toBeTruthy();
    const own = screen.getByRole("heading", { level: 4, name: "This wallet, across its chains" });
    const bitcoin = screen.getByRole("heading", { level: 4, name: "Bitcoin" });
    expect(own.compareDocumentPosition(bitcoin) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy(); // the wallet's own first
    const tied = screen.getByText("Tied to your public wallet Exchange withdrawals (70%)").closest("details")!;
    expect(within(tied).getByText("-49")).toBeTruthy();
    expect(tied.textContent).toContain("with 70% confidence");
    const reuse = screen.getByText("Reused addresses: 1").closest("details")!;
    expect(within(reuse).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["https://mempool.space/tx/b1", "https://mempool.space/tx/b3"]);
    expect(screen.queryByText("Partly checked")).toBeNull();
  });

  it("shows an account wallet's findings under the chain they were found on, with no deep-scan note", () => {
    renderTracker(<WalletPanel address={HOT} />);
    expect(screen.getByText("C")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 4, name: "Ethereum" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 4, name: "This wallet, across its chains" })).toBeNull();
    expect(screen.getByText("Dealt directly with an exchange: Binance")).toBeTruthy();
    expect(screen.getByText("Dealt with a mixer: Tornado Cash Router")).toBeTruthy();
    expect(screen.queryByText("Wallet checks; addresses not deep-scanned")).toBeNull();
  });

  it("lists the address's first funder under its chain family", () => {
    const hot = ANALYSIS.exposure![HOT]!;
    const funded = { ...hot.findings[0]!, id: "x-funder-mine", severity: "medium" as const, scoreImpact: -10, params: { funder: "Savings", chains: "Ethereum" } };
    const exposure = { ...ANALYSIS.exposure, [HOT]: { ...hot, by_chain: { ...hot.by_chain, evm: [funded] } } };
    renderTracker(<WalletPanel address={HOT} />, { analysis: { ...ANALYSIS, exposure } });
    expect(screen.getByRole("heading", { level: 4, name: "EVM" })).toBeTruthy();
    expect(screen.getByText("First funded by your wallet Savings")).toBeTruthy();
  });

  it("says a grade is partly checked, why, and that no finding is no proof", () => {
    const partial = { ...ANALYSIS.exposure![PHONE]!, partial: true, gaps: [{ id: "unread" as const, params: { check: "funder" } }] };
    renderTracker(<WalletPanel address={PHONE} />, { analysis: { ...ANALYSIS, exposure: { ...ANALYSIS.exposure, [PHONE]: partial } } });
    expect(screen.getByText("Partly checked")).toBeTruthy();
    expect(screen.getByText("Not read yet: funder. The page makes no call; Privacy score reads it.")).toBeTruthy();
    expect(screen.getByText("No finding among the checks made here. That is no proof of privacy.")).toBeTruthy();
  });

  it("gives no grade to a wallet never read", () => {
    renderTracker(<WalletPanel address={PHONE} />, { analysis: { ...ANALYSIS, exposure: { ...ANALYSIS.exposure, [PHONE]: null } } });
    expect(screen.getByText("No grade yet: sync this wallet")).toBeTruthy();
    expect(screen.queryByText("A+")).toBeNull();
  });

  it("names the wallets a link runs through", () => {
    const verdicts = ANALYSIS.verdicts.map((v) => (v.address === HOT ? { ...v, links: [{ ...v.links[0]!, via: [PHONE] }] } : v));
    renderTracker(<WalletPanel address={HOT} />, { analysis: { ...ANALYSIS, verdicts } });
    expect(screen.getByRole("button", { name: /Savings \(private\)/ }).textContent).toContain("1 hop, transfer, through your wallet Phone");
  });

  it("lists the caveats, tokens with their trust and the fakes apart", () => {
    renderTracker(<WalletPanel address={HOT} />);
    expect(screen.getByText(/It dealt directly with Binance\./)).toBeTruthy();
    expect(screen.getByText("It dealt with a mixer. Funds past a mixer cannot be followed here.")).toBeTruthy();
    expect(screen.getByText("Paths longer than 4 hops are not searched.")).toBeTruthy();
    expect(screen.getByText(/^Not read on arbitrum, polygon\./)).toBeTruthy();
    expect(screen.getByText("verified")).toBeTruthy();
    cleanup();
    renderTracker(<WalletPanel address={SAVINGS} />);
    expect(screen.getByText("1 fake token · worth 0")).toBeTruthy();
    // The fake USDC is only in the folded list of fakes.
    expect(screen.getAllByText("USDC · ethereum").map((el) => Boolean(el.closest("details")))).toEqual([true]);
  });

  it("says a clean wallet is no proof of privacy, and that no transfers came in", () => {
    renderTracker(<WalletPanel address={PHONE} />);
    expect(screen.getByText("No link to your other wallets found")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "This is not proof of privacy" })).toBeTruthy();
    expect(screen.getByText("No incoming transfers known.")).toBeTruthy();
  });

  it("follows each source of its funds to the path it took", () => {
    const { tracker } = renderTracker(<WalletPanel address={COLD} />);
    expect(screen.getByRole("heading", { name: "Where its funds came from" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "0.49 BTC received on bitcoin" })).toBeTruthy();
    const own = screen.getByRole("button", { name: /Exchange withdrawals · My wallet, public/ });
    expect(own.textContent).toContain("80%");
    expect(own.textContent).toContain("0.39 BTC · 2 hops");
    expect(screen.getByRole("button", { name: /1A1zP1e…ivfNa · Unknown address/ }).textContent).toContain(
      "0.1 BTC · 1 hop · followed no further: no earlier transfer of this asset",
    );
    fireEvent.click(own);
    const source = ANALYSIS.origins[COLD]![0]!.sources[0]!;
    expect(tracker.showPath).toHaveBeenCalledWith(source.path, "Origin of BTC in Cold storage", COLD);
    expect(screen.queryByText(/smaller sources/)).toBeNull();
  });

  it("names the share of smaller sources", () => {
    const [btc] = ANALYSIS.origins[COLD]!;
    const origins = { ...ANALYSIS.origins, [COLD]: [{ ...btc!, sources: btc!.sources.slice(0, 1) }] };
    renderTracker(<WalletPanel address={COLD} />, { analysis: { ...ANALYSIS, origins } });
    expect(screen.getByText("20% from smaller sources not listed (the empty end of the bar)")).toBeTruthy();
  });

  it("names the bridge money crossed, on its source and in the path it opens", () => {
    const binance = "0x28c6c06298d514db089934071355e5743bf21d60";
    const bridged = {
      chain: "solana", asset: "ETH", token: "", bridged_from: "ethereum", received: 1.5,
      sources: [{
        address: binance, name: "Binance 14", kind: "cex", guess: false, type: "", public: false, reason: "",
        share: 1, amount: 1.5, path: [PHONE, HOT, binance], via: ["Wormhole"],
      }],
    };
    const origins = { ...ANALYSIS.origins, [PHONE]: [bridged] };
    const { tracker } = renderTracker(<WalletPanel address={PHONE} />, { analysis: { ...ANALYSIS, origins } });
    expect(screen.getByRole("heading", { name: "1.5 ETH bridged from ethereum to solana" })).toBeTruthy();
    const source = screen.getByRole("button", { name: /Binance 14/ });
    expect(source.textContent).toContain("1.5 ETH · 2 hops · via Wormhole");
    fireEvent.click(source);
    expect(tracker.showPath).toHaveBeenCalledWith([PHONE, HOT, binance], "Origin of ETH in Phone · via Wormhole", PHONE);
  });

  it("lists an HD wallet's addresses, each with its deep scan", () => {
    const cold = GRAPH.nodes.find((n) => n.address === COLD)!;
    const fakeBalance = { chain: "bitcoin", asset: "XYZ", amount: 9, token: "x", trust: { level: "scam" as const, reason: "airdrop" } };
    const hd: GraphNode = {
      ...cold,
      wallet: "hd:abc",
      addresses: [{ address: COLD, balances: cold.balances }, { address: "bc1qsecond", balances: [fakeBalance] }],
    };
    const { tracker } = renderTracker(<WalletPanel address={COLD} />, { byAddress: new Map([[COLD, hd]]) });
    expect(screen.getByRole("heading", { name: "2 addresses" })).toBeTruthy();
    expect(screen.getByText("bc1qsecond")).toBeTruthy();
    expect(screen.getAllByText("fake · worth 0")).toHaveLength(1);
    expect(screen.queryByRole("link", { name: "Privacy score" })).toBeNull();
    // Each address keeps its deep scan one click away.
    expect(screen.getAllByRole("link", { name: "Deep scan" }).map((a) => a.getAttribute("href"))).toEqual([`/#addr=${encodeURIComponent(COLD)}`, "/#addr=bc1qsecond"]);
    fireEvent.click(button("Re-sync"));
    expect(tracker.startSync).toHaveBeenCalledWith(["hd:abc"]);
    fireEvent.click(button("Make public"));
    expect(api).toHaveBeenCalledWith("wallets", { address: COLD, name: "Cold storage", public: true });
  });

  it("addresses an HD wallet none of whose addresses is known by its id", () => {
    const empty: GraphNode = { address: "hd:empty", wallet: "hd:empty", name: "Empty HD", public: false, mine: 1, addresses: [] };
    const verdict = { ...ANALYSIS.verdicts[0]!, address: "hd:empty", wallet: "hd:empty", name: "Empty HD", checked: "none" as const, links: [] };
    const { tracker } = renderTracker(<WalletPanel address="hd:empty" />, {
      byAddress: new Map([["hd:empty", empty]]),
      analysis: { ...ANALYSIS, verdicts: [verdict] },
    });
    expect(screen.getByText("Not checked yet: sync this wallet")).toBeTruthy();
    fireEvent.click(button("Make public"));
    expect(api).toHaveBeenCalledWith("wallets", { address: "hd:empty", name: "Empty HD", public: true });
    fireEvent.click(button("Re-sync"));
    expect(tracker.startSync).toHaveBeenCalledWith(["hd:empty"]);
    expect(screen.queryByRole("heading", { name: /addresses/ })).toBeNull();
  });

  it("shows nothing for an address that is not one of my wallets", () => {
    const { container } = renderTracker(<WalletPanel address="nope" />);
    expect(container.textContent).toBe("");
  });
});
