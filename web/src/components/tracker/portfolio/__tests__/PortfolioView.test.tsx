// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, screen, within } from "@testing-library/react";

vi.mock("react-i18next", async () => (await import("../../__tests__/i18n-mock")).i18nMock());

import { ANALYSIS, GRAPH, renderTracker } from "../../__tests__/harness";
import type { GraphNode } from "@/lib/tracker/types";
import { PortfolioView } from "../PortfolioView";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

const COLD = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq";
const card = (name: string) => screen.getByRole("button", { name }).closest("li")!;
const rowOf = (cell: HTMLElement) => [...cell.closest("tr")!.querySelectorAll("th, td")].map((c) => c.textContent);

describe("PortfolioView", () => {
  it("shows the hero, the fiat totals and both vaults", () => {
    renderTracker(<PortfolioView />);
    expect(screen.getByRole("heading", { name: "Who can tell it’s yours?" })).toBeTruthy();
    expect(within(screen.getByRole("list", { name: "How this app works" })).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Read-only",
      "Self-hosted",
      "No funny business",
    ]);
    const totals = [...document.querySelectorAll("dl > div")].map((d) => d.textContent);
    expect(totals).toEqual(["Total$68,740", "Private$57,100", "Public$11,640"]);
    expect(screen.getByRole("heading", { name: "Private 4 wallets" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Public 1 wallet" })).toBeTruthy();
  });

  it("lists every asset with its value and split, the fake one after the rest, worth 0 and never summed into the real one", () => {
    renderTracker(<PortfolioView />);
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["Asset", "Total", "Private", "Public", "Value", "Split", "Chains"]);
    const heads = within(table).getAllByRole("rowheader");
    expect(heads.map((h) => h.textContent)).toEqual(["BTC", "ETH", "SOL", "USDC", expect.stringContaining("USDC")]);
    expect(rowOf(heads[0]!)).toEqual(["BTC", "0.61", "0.49", "0.12", "$59,170", "", "bitcoin"]);
    expect(rowOf(heads[3]!)).toEqual(["USDC", "1,200", "1,200", "0", "$1,200", "", "ethereum"]);
    expect(rowOf(heads[4]!).slice(1, 5)).toEqual(["50,000", "50,000", "0", "$0.00"]);
    expect(within(heads[4]!).getByText("fake · worth 0")).toBeTruthy();
    expect(screen.getByRole("img", { name: "80% private, 20% public" })).toBeTruthy();
  });

  it("drops the totals, the value column and wallet values without fiat", () => {
    renderTracker(<PortfolioView />, { analysis: { ...ANALYSIS, fiat: null } });
    expect(document.querySelector("dl")).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Value" })).toBeNull();
    expect(within(card("Cold storage")).queryByText(/\$/)).toBeNull();
  });

  it("gives a card per wallet that opens it", () => {
    const { tracker } = renderTracker(<PortfolioView />);
    expect(within(card("Cold storage").closest("ul")!).getAllByRole("listitem")).toHaveLength(5);
    const cold = card("Cold storage");
    expect(cold.textContent).toContain("bc1qar0…f5mdq");
    expect(cold.textContent).toContain("$47,530");
    expect(cold.textContent).toContain("Linked to public wallet Exchange withdrawals (70%)");
    expect(cold.textContent).toContain("BTC came from");
    expect(within(cold).getByRole("img", { name: "80% My wallet, public, 20% Unknown address" })).toBeTruthy();
    expect(card("Exchange withdrawals").textContent).toContain("Tied to 1 other wallet of yours");
    // The fake USDC is not one of the wallet's amounts.
    expect(card("Savings").textContent).not.toContain("50,000");
    fireEvent.click(screen.getByRole("button", { name: "Cold storage" }));
    expect(tracker.openWallet).toHaveBeenCalledWith(COLD);
  });

  it("marks an unchecked wallet's value unknown and a partly read one as at least", () => {
    const verdicts = ANALYSIS.verdicts.map((v) =>
      v.address === COLD ? { ...v, checked: "partial" as const } : v.name === "Phone" ? { ...v, checked: "none" as const } : v,
    );
    renderTracker(<PortfolioView />, { analysis: { ...ANALYSIS, verdicts } });
    expect(card("Cold storage").textContent).toContain("≥ $47,530");
    expect(within(card("Phone")).getByTitle("Not checked, so its value is unknown").textContent).toBe("–");
    expect(card("Phone").textContent).toContain("Not checked yet: sync this wallet");
  });

  it("counts the addresses of an HD wallet, also one none of whose addresses is known yet", () => {
    const hd: GraphNode = { ...GRAPH.nodes.find((n) => n.address === COLD)!, addresses: [{ address: COLD }, { address: "bc1qother" }] };
    const byAddress = new Map(GRAPH.nodes.map((n) => [n.address, n.address === COLD ? hd : n]));
    byAddress.set("hd:empty", { address: "hd:empty", wallet: "hd:empty", name: "Empty HD", public: false, mine: 1, addresses: [] });
    const empty = { ...ANALYSIS.verdicts[0]!, address: "hd:empty", wallet: "hd:empty", name: "Empty HD", checked: "none" as const, links: [] };
    renderTracker(<PortfolioView />, { byAddress, analysis: { ...ANALYSIS, verdicts: [...ANALYSIS.verdicts, empty] } });
    expect(card("Cold storage").textContent).toContain("2 addresses");
    expect(card("Empty HD").textContent).toContain("hd:empty");
  });

  it("says nothing about my wallets before they have been read once", () => {
    renderTracker(<PortfolioView />, { loaded: false, graph: { nodes: [], edges: [], links: [] }, analysis: { ...ANALYSIS, verdicts: [] } });
    expect(screen.queryByText("Add your wallets under “Wallets & sync” to start.")).toBeNull();
    expect(screen.queryByText("Nothing here yet.")).toBeNull();
    expect(screen.getByText("Who can tell it’s yours?")).toBeTruthy();
  });

  it("says what to do when there are no wallets or no balances yet", () => {
    renderTracker(<PortfolioView />, { graph: { nodes: [], edges: [], links: [] }, analysis: { ...ANALYSIS, verdicts: [] } });
    expect(screen.getByText("Add your wallets under “Wallets & sync” to start.")).toBeTruthy();
    expect(screen.getAllByText("Nothing here yet.")).toHaveLength(2);
    expect(screen.getAllByText("Know what you hold").length).toBeGreaterThan(1);
    cleanup();
    const bare = GRAPH.nodes.filter((n) => n.mine).map((n) => ({ ...n, balances: [] }));
    renderTracker(<PortfolioView />, { graph: { ...GRAPH, nodes: bare } });
    expect(screen.getByText("No balances yet: sync your wallets.")).toBeTruthy();
  });

  it("runs the ticker over the asset totals, and keeps its pause in this browser", () => {
    renderTracker(<PortfolioView />);
    expect(screen.getAllByText("SOL").length).toBeGreaterThan(1);
    const pause = screen.getByRole("button", { name: "Pause the ticker" });
    expect(pause.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(pause);
    expect(localStorage.getItem("tracker-ticker")).toBe("paused");
    const play = screen.getByRole("button", { name: "Play the ticker" });
    expect(play.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(play);
    expect(localStorage.getItem("tracker-ticker")).toBe("");
    expect(screen.getByRole("button", { name: "Pause the ticker" })).toBeTruthy();
  });
});
