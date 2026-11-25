// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";

vi.mock("react-i18next", async () => (await import("../../__tests__/i18n-mock")).i18nMock());
vi.mock("@/lib/tracker/api", () => ({ api: vi.fn(async () => ({ ok: true })) }));
const { lookUp } = vi.hoisted(() => ({ lookUp: vi.fn(async (_text: string) => {}) }));
vi.mock("@/components/tracker/lookup/useLookup", () => ({ useLookup: () => ({ lookUp, openLookup: vi.fn() }) }));

import { api } from "@/lib/tracker/api";
import type { GraphNode, Verdict } from "@/lib/tracker/types";
import { ANALYSIS, GRAPH, renderTracker } from "../../__tests__/harness";
import { ManageView } from "../ManageView";

const apiMock = vi.mocked(api);
const COLD = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq";
const PUBLIC = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh";
const WATCHED = "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy";

const rowOf = (name: string | RegExp) => screen.getByRole("button", { name }).closest("li") as HTMLElement;
const card = (heading: string) => screen.getByRole("heading", { name: heading }).parentElement as HTMLElement;

beforeEach(() => apiMock.mockClear());
afterEach(cleanup);

// Two HD wallets: one with addresses found, one none of whose addresses is known yet.
const verdict = (v: Partial<Verdict>): Verdict => ({ ...ANALYSIS.verdicts[0]!, ...v });
function withHd() {
  const found: GraphNode = { address: "bc1qhd0first", wallet: "hd:abc", name: "Trezor", public: 0, mine: 1, chain: "bitcoin", addresses: [{ address: "bc1qhd0first" }, { address: "bc1qhd0second" }, { address: "bc1qhd0third" }] };
  const empty: GraphNode = { address: "hd:new", wallet: "hd:new", name: "Fresh xpub", public: true, mine: 1, addresses: [] };
  const byAddress = new Map<string, GraphNode>([[found.address, found], [empty.address, empty]]);
  const verdicts = [verdict({ address: found.address, wallet: "hd:abc", name: "Trezor" }), verdict({ address: "hd:new", wallet: "hd:new", name: "Fresh xpub", public: true, checked: "none" })];
  return { byAddress, analysis: { ...ANALYSIS, verdicts } };
}

describe("ManageView", () => {
  it("shows the hero and one row per wallet of mine", () => {
    renderTracker(<ManageView />);
    expect(screen.getByRole("heading", { name: "Round them up" })).toBeTruthy();
    expect(screen.getByText("Wallets & sync")).toBeTruthy();
    const list = within(card("My wallets")).getAllByRole("listitem");
    expect(list).toHaveLength(ANALYSIS.verdicts.length);
    const cold = rowOf(/Cold storage/);
    expect(within(cold).getByText("bc1qar0…f5mdq")).toBeTruthy();
    expect(within(cold).getByText("History complete")).toBeTruthy();
    expect(within(cold).getByText("private")).toBeTruthy();
    expect(within(cold).getByRole("button", { name: "Make public" })).toBeTruthy();
    expect(within(rowOf(/Exchange withdrawals/)).getByRole("button", { name: "Make private" })).toBeTruthy();
    expect(within(cold).queryByText("sync error")).toBeNull();
  });

  it("opens a wallet, toggles it public and removes it through act", () => {
    const { tracker } = renderTracker(<ManageView />);
    fireEvent.click(screen.getByRole("button", { name: /Cold storage/ }));
    expect(tracker.openWallet).toHaveBeenCalledWith(COLD);

    fireEvent.click(within(rowOf(/Cold storage/)).getByRole("button", { name: "Make public" }));
    expect(apiMock).toHaveBeenLastCalledWith("wallets", { address: COLD, name: "Cold storage", public: true });
    fireEvent.click(within(rowOf(/Exchange withdrawals/)).getByRole("button", { name: "Make private" }));
    expect(apiMock).toHaveBeenLastCalledWith("wallets", { address: PUBLIC, name: "Exchange withdrawals", public: false });
    fireEvent.click(within(rowOf(/Cold storage/)).getByRole("button", { name: "Remove" }));
    expect(apiMock).toHaveBeenLastCalledWith(`wallets/${COLD}`, undefined, "DELETE");
  });

  it("runs every change through act, so the data is read again after it", () => {
    renderTracker(<ManageView />, { act: vi.fn(() => () => {}) });
    for (const name of ["Make public", "Make private", "Remove"]) for (const b of screen.getAllByRole("button", { name })) fireEvent.click(b);
    expect(apiMock).not.toHaveBeenCalled();
  });

  it("counts an HD wallet's addresses and acts on it by its id", () => {
    renderTracker(<ManageView />, withHd());
    const trezor = rowOf(/Trezor/);
    expect(within(trezor).getByText("3 addresses")).toBeTruthy();
    fireEvent.click(within(trezor).getByRole("button", { name: "Make public" }));
    expect(apiMock).toHaveBeenLastCalledWith("wallets", { address: "bc1qhd0first", name: "Trezor", public: true });
    fireEvent.click(within(trezor).getByRole("button", { name: "Remove" }));
    expect(apiMock).toHaveBeenLastCalledWith("wallets/hd%3Aabc", undefined, "DELETE");

    const fresh = rowOf(/Fresh xpub/);
    expect(within(fresh).getByText("0 addresses")).toBeTruthy();
    expect(within(fresh).queryByText("History complete")).toBeNull();
    fireEvent.click(within(fresh).getByRole("button", { name: "Make private" }));
    expect(apiMock).toHaveBeenLastCalledWith("wallets", { address: "hd:new", name: "Fresh xpub", public: false });
  });

  it("marks a wallet whose sync failed, with the error as its title", () => {
    const verdicts = ANALYSIS.verdicts.map((v) => (v.address === COLD ? { ...v, coverage: { ...v.coverage, error: "esplora: 429 Too Many Requests" } } : v));
    const byAddress = new Map(GRAPH.nodes.map((n) => [n.address, n.address === PUBLIC ? { ...n, error: "timed out" } : n]));
    renderTracker(<ManageView />, { analysis: { ...ANALYSIS, verdicts }, byAddress });
    expect(within(rowOf(/Cold storage/)).getByText("sync error").getAttribute("title")).toBe("esplora: 429 Too Many Requests");
    expect(within(rowOf(/Exchange withdrawals/)).getByText("sync error").getAttribute("title")).toBe("timed out");
  });

  it("says when there are no wallets and nothing watched", () => {
    renderTracker(<ManageView />, { analysis: { ...ANALYSIS, verdicts: [], watched: [] } });
    expect(within(card("My wallets")).getByText("None yet.")).toBeTruthy();
    expect(screen.getByText("None yet. Look an address up and press Watch.")).toBeTruthy();
  });

  it("says nothing of the sort before the wallets have been read once", () => {
    renderTracker(<ManageView />, { loaded: false, analysis: { ...ANALYSIS, verdicts: [], watched: [] } });
    expect(screen.queryByText("None yet.")).toBeNull();
    expect(screen.queryByText("None yet. Look an address up and press Watch.")).toBeNull();
  });

  it("looks up a watched address and stops watching it", () => {
    renderTracker(<ManageView />);
    const row = rowOf(new RegExp(WATCHED.slice(0, 7)));
    expect(within(row).getByText("watched")).toBeTruthy();
    expect(within(row).getByText(WATCHED)).toBeTruthy();
    fireEvent.click(within(row).getAllByRole("button")[0]!);
    expect(lookUp).toHaveBeenCalledWith(WATCHED);
    fireEvent.click(within(row).getByRole("button", { name: "Remove" }));
    expect(apiMock).toHaveBeenLastCalledWith(`watch/${WATCHED}`, undefined, "DELETE");
  });

  it("lists the chains, read or waiting for a key", () => {
    renderTracker(<ManageView />);
    const chains = card("Chains");
    const bitcoin = within(chains).getByText("Bitcoin").closest("li") as HTMLElement;
    expect(within(bitcoin).getByText("read")).toBeTruthy();
    const base = within(chains).getByText("Base").closest("li") as HTMLElement;
    expect(within(base).getByText("needs key")).toBeTruthy();
    expect(within(base).getByText("Needs a free Blockscout key (CT_BLOCKSCOUT_KEY).")).toBeTruthy();
  });

  it("adds a wallet, then clears the form and refreshes", async () => {
    const { tracker } = renderTracker(<ManageView />);
    const address = screen.getByRole("textbox", { name: "Address" }) as HTMLInputElement;
    fireEvent.change(address, { target: { value: "zpub6rFR7y4Q2AijBEqTUquhVz398htDFrtymD9xYYfG1m4wAcvPhXNfE3EfH1r1ADqtfSdVCToUG868RvUUkgDKf31mGDtKsAYz2oz2AGutZYs" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Name" }), { target: { value: "Coldcard" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /Public: tied to my identity/ }));
    fireEvent.click(screen.getByRole("button", { name: "Add wallet" }));
    expect(apiMock).toHaveBeenLastCalledWith("wallets", { address: expect.stringMatching(/^zpub6rFR/), name: "Coldcard", public: true });
    await waitFor(() => expect(tracker.refresh).toHaveBeenCalled());
    expect(tracker.setStatus).toHaveBeenCalledWith({ text: "", level: "" });
    expect(address.value).toBe("");
  });

  it("keeps the form and reports the server's error", async () => {
    apiMock.mockRejectedValueOnce(new Error("not an address"));
    const { tracker } = renderTracker(<ManageView />);
    const address = screen.getByRole("textbox", { name: "Address" }) as HTMLInputElement;
    fireEvent.change(address, { target: { value: "nonsense" } });
    fireEvent.click(screen.getByRole("button", { name: "Add wallet" }));
    expect(apiMock).toHaveBeenLastCalledWith("wallets", { address: "nonsense", name: "", public: false });
    await waitFor(() => expect(tracker.fail).toHaveBeenCalledWith(new Error("not an address")));
    expect(tracker.refresh).not.toHaveBeenCalled();
    expect(address.value).toBe("nonsense");
  });

  it("syncs all my wallets at the chosen depth, not while a sync runs", () => {
    const { tracker } = renderTracker(<ManageView />);
    fireEvent.click(screen.getByRole("button", { name: "Sync" }));
    expect(tracker.startSync).toHaveBeenLastCalledWith([], 0, false);
    fireEvent.change(screen.getByRole("combobox", { name: "Depth" }), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "Also download open label sets (first time)" }));
    fireEvent.click(screen.getByRole("button", { name: "Sync" }));
    expect(tracker.startSync).toHaveBeenLastCalledWith([], 2, true);
    cleanup();

    renderTracker(<ManageView />, { sync: { running: true, done: 1, total: 5, current: "labels", error: "", notes: [], filling: "" } });
    expect((screen.getByRole("button", { name: "Sync" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
