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

  it("lists an HD wallet's addresses, with no privacy score", () => {
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
