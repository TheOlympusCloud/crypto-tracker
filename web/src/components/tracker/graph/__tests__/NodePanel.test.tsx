// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";

vi.mock("react-i18next", async () => (await import("../../__tests__/i18n-mock")).i18nMock());
vi.mock("@/lib/tracker/api", () => ({ api: vi.fn(async () => ({})) }));
const lookUp = vi.hoisted(() => vi.fn(async () => {}));
vi.mock("../../lookup/useLookup", () => ({ useLookup: () => ({ lookUp, openLookup: vi.fn() }) }));

import { api } from "@/lib/tracker/api";
import type { GraphNode, LookupResult } from "@/lib/tracker/types";
import { GRAPH, renderTracker } from "../../__tests__/harness";
import { NodePanel } from "../NodePanel";

const COLD = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq";
const STRANGER = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa";
const BINANCE = "0x28c6c06298d514db089934071355e5743bf21d60";
const node = (address: string) => GRAPH.nodes.find((n) => n.address === address)!;
const button = (name: string | RegExp) => screen.getByRole("button", { name });
const row = (term: string) => screen.getByText(term, { selector: "dt" }).nextElementSibling?.textContent;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("NodePanel", () => {
  it("shows one of my wallets with its facts and actions", () => {
    const { tracker } = renderTracker(<NodePanel address={COLD} />);
    expect(screen.getByRole("heading", { name: "Cold storage" })).toBeTruthy();
    expect(screen.getByText("private")).toBeTruthy();
    expect(screen.getByText(COLD)).toBeTruthy();
    expect(row("Type")).toBe("My wallet, private");
    expect(row("Chain")).toBe("Bitcoin");
    expect(row("Synced")).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(row("BTC · bitcoin")).toBeTruthy();
    expect(screen.queryByText("Named by")).toBeNull();
    expect(screen.queryByRole("button", { name: "Look up" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Save label" })).toBeNull();

    fireEvent.click(button("Verdict and origin"));
    expect(tracker.openWallet).toHaveBeenCalledWith(COLD);
    fireEvent.click(button("Re-sync"));
    expect(tracker.startSync).toHaveBeenCalledWith([COLD]);
    fireEvent.click(button("Fold its counterparties"));
    expect(tracker.toggleFold).toHaveBeenCalledWith(COLD);
    expect(screen.getByRole("link", { name: "Privacy score" }).getAttribute("href")).toBe(`/#addr=${COLD}`);
    const explorer = screen.getByRole("link", { name: "Explorer" });
    expect(explorer.getAttribute("href")).toBe(`https://mempool.space/address/${COLD}`);
    expect(explorer.getAttribute("target")).toBe("_blank");
  });

  it("lists the privacy findings of a wallet of mine, and none for a stranger", () => {
    renderTracker(<NodePanel address={COLD} />);
    expect(screen.getByRole("heading", { name: "Privacy grade" })).toBeTruthy();
    expect(screen.getByText("34 of 100")).toBeTruthy();
    expect(screen.getByText("Tied to your public wallet Exchange withdrawals (70%)")).toBeTruthy();
    expect(screen.getByText("Reused addresses: 1")).toBeTruthy();
    cleanup();
    renderTracker(<NodePanel address={STRANGER} />);
    expect(screen.queryByRole("heading", { name: "Privacy grade" })).toBeNull();
  });

  it("finds the score of an HD wallet from any of its addresses", () => {
    const second: GraphNode = { ...node(COLD), address: "bc1qsecond", wallet: COLD };
    renderTracker(<NodePanel address="bc1qsecond" />, { byAddress: new Map([["bc1qsecond", second]]) });
    expect(screen.getByText("Reused addresses: 1")).toBeTruthy();
  });

  it("says how many counterparties a folded address hides", () => {
    renderTracker(<NodePanel address={COLD} />, { collapsed: new Set([COLD]) });
    expect(button("Unfold (1 hidden)").getAttribute("aria-pressed")).toBe("true");
    cleanup();
    renderTracker(<NodePanel address={STRANGER} />, { collapsed: new Set([STRANGER]) });
    expect(button("Unfold")).toBeTruthy();
  });

  it("shows a labelled counterparty, where its name comes from, and lets it be claimed or looked up", () => {
    const { tracker } = renderTracker(<NodePanel address={BINANCE} />);
    expect(screen.getByRole("heading", { name: "Binance 14" })).toBeTruthy();
    expect(row("Type")).toBe("exchange");
    expect(row("Named by")).toBe("an open label set");
    expect(row("Label")).toBe("Binance 14");
    expect(row("Note")).toMatch(/^very active address/);
    expect(screen.getByRole("link", { name: "Privacy score" }).getAttribute("href")).toMatch(/^\/tracker\/scan\/#addr=0x/); // the tracker's scan

    fireEvent.click(button("This is mine"));
    expect(tracker.act).toHaveBeenCalled();
    expect(api).toHaveBeenCalledWith("wallets", { address: BINANCE, name: "", public: false });
    fireEvent.click(button("Look up"));
    expect(lookUp).toHaveBeenCalledWith(BINANCE);
  });

  it("saves a label for an address that is not mine", () => {
    renderTracker(<NodePanel address={BINANCE} />);
    const name = screen.getByRole("textbox", { name: "Label" });
    expect((name as HTMLInputElement).value).toBe("Binance 14");
    expect((screen.getByRole("combobox", { name: "Type" }) as HTMLSelectElement).value).toBe("cex");
    fireEvent.change(name, { target: { value: "Hot exchange" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Type" }), { target: { value: "dex" } });
    fireEvent.click(button("Save label"));
    expect(api).toHaveBeenCalledWith("labels", { address: BINANCE, name: "Hot exchange", category: "dex" });
  });

  it("offers to expand an address not synced yet, and marks a fake token", () => {
    const fresh: GraphNode = {
      ...node(STRANGER),
      synced_at: null,
      error: "rate limited",
      balances: [{ chain: "bitcoin", asset: "USDT", amount: 1e20, token: "0xfake", trust: { level: "scam", reason: "Dressed as a real token" } }],
    };
    const { tracker } = renderTracker(<NodePanel address={STRANGER} />, { byAddress: new Map([[STRANGER, fresh]]) });
    expect(row("Type")).toBe("Unknown address");
    expect(row("Synced")).toBe("not yet, so its own counterparties are unknown");
    expect(row("Error")).toBe("rate limited");
    expect(screen.getByText("fake · worth 0")).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Label" }).getAttribute("placeholder")).toBe("e.g. Kraken deposit");
    fireEvent.click(button("Expand"));
    expect(tracker.startSync).toHaveBeenCalledWith([STRANGER]);
  });

  it("finds the address in a lookup's graph and leads back to the lookup", async () => {
    const scope = {
      ...node(BINANCE),
      chains: ["ethereum"],
      watched: false,
      assets: [],
      value: null,
      currency: "EUR",
      unvouched: 0,
      risk: { level: "warn", headline: "", reason: "", reasons: [] },
      connections: [],
      more: 0,
      truncated: false,
      errors: [],
      notes: [],
      graph: { nodes: [node(BINANCE), { ...node(STRANGER), name: "Only in the lookup" }], edges: [], links: [] },
    } as LookupResult;
    const { tracker } = renderTracker(<NodePanel address={STRANGER} />, { scope });
    expect(screen.getByRole("heading", { name: "Only in the lookup" })).toBeTruthy();
    fireEvent.click(button("Back to Binance 14"));
    expect(tracker.openPanel).toHaveBeenCalledWith({ kind: "lookup" });
    expect(screen.queryByRole("button", { name: "Re-sync" })).toBeNull();

    // The looked-up wallet itself: no "Look up" of itself, and its new label is kept in the lookup.
    cleanup();
    const { tracker: again } = renderTracker(<NodePanel address={BINANCE} />, { scope });
    expect(screen.queryByRole("button", { name: "Look up" })).toBeNull();
    fireEvent.change(screen.getByRole("textbox", { name: "Label" }), { target: { value: "" } });
    fireEvent.click(button("Save label"));
    await waitFor(() => expect(again.refresh).toHaveBeenCalled());
    const renamed = vi.mocked(again.setScope).mock.calls[0]?.[0];
    expect(renamed?.label).toBe("");
    expect(renamed?.category).toBeUndefined();
    expect(renamed?.graph.nodes[0]?.label).toBe("");
  });

  it("keeps the old label on screen when the server refuses the new one", async () => {
    vi.mocked(api).mockRejectedValueOnce(new Error("category must be one of ..."));
    const scope = { ...node(BINANCE), chains: ["ethereum"], watched: false, assets: [], value: null, currency: "USD", unvouched: 0,
      risk: { level: "ok", headline: "", reason: "", reasons: [] }, connections: [], more: 0, truncated: false, errors: [], notes: [],
      graph: { nodes: [node(BINANCE)], edges: [], links: [] } } as LookupResult;
    const { tracker } = renderTracker(<NodePanel address={BINANCE} />, { scope });
    fireEvent.change(screen.getByRole("textbox", { name: "Label" }), { target: { value: "Renamed" } });
    fireEvent.click(button("Save label"));
    await waitFor(() => expect(tracker.fail).toHaveBeenCalled());
    expect(tracker.setScope).not.toHaveBeenCalled();
    expect(tracker.refresh).not.toHaveBeenCalled();
  });

  it("does not bring a closed lookup back when its label is saved late", async () => {
    let saved: (v: unknown) => void = () => {};
    vi.mocked(api).mockImplementationOnce(() => new Promise((done) => (saved = done)));
    const scope = { ...node(BINANCE), chains: ["ethereum"], watched: false, assets: [], value: null, currency: "USD", unvouched: 0,
      risk: { level: "ok", headline: "", reason: "", reasons: [] }, connections: [], more: 0, truncated: false, errors: [], notes: [],
      graph: { nodes: [node(BINANCE)], edges: [], links: [] } } as LookupResult;
    const { tracker } = renderTracker(<NodePanel address={BINANCE} />, { scope });
    fireEvent.change(screen.getByRole("textbox", { name: "Label" }), { target: { value: "Renamed" } });
    fireEvent.click(button("Save label"));
    tracker.closePanel(); // Escape, before the server answers
    saved({});
    await waitFor(() => expect(tracker.refresh).toHaveBeenCalled());
    expect(tracker.setScope).not.toHaveBeenCalled();
  });

  it("shows nothing for an address it does not know", () => {
    const { container } = renderTracker(<NodePanel address="nowhere" />);
    expect(container.textContent).toBe("");
  });
});
