// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, screen, within } from "@testing-library/react";

vi.mock("react-i18next", async () => (await import("../../__tests__/i18n-mock")).i18nMock());

import { ANALYSIS, renderTracker } from "../../__tests__/harness";
import type { Analysis, WalletGroup } from "@/lib/tracker/types";
import { GroupCards, GroupPanel } from "../Groups";

afterEach(cleanup);

const COLD = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq";
const PUBLIC = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh";
const HOT = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045";
const SAVINGS = "0xab5801a7d398351b8be11c439e05c5b3259aec9b";
const PHONE = "vines1vzrYbzLMRdu58ou5XTby4qAqVRLmqo36NKPTg";

// Hot wallet, Savings and Phone tied in a triangle, Cold storage hanging on Hot wallet alone.
const FOUR: WalletGroup = {
  wallets: [HOT, SAVINGS, COLD, PHONE],
  public: [],
  chains: ["bitcoin", "ethereum", "solana"],
  checked: "partial",
  links: [
    { a: HOT, b: PHONE, confidence: 0.55, path: [HOT, PHONE], kinds: ["exchange-flow"], apart: null },
    { a: SAVINGS, b: PHONE, confidence: 0.6, path: [SAVINGS, "x", PHONE], kinds: ["transfer", "transfer"], apart: null },
    { a: HOT, b: COLD, confidence: 0.8, path: [HOT, COLD], kinds: ["bridge"], apart: [[HOT, SAVINGS, PHONE], [COLD]] },
    { a: HOT, b: SAVINGS, confidence: 1, path: [HOT, SAVINGS], kinds: ["transfer"], apart: null },
  ],
};
const with_ = (groups: WalletGroup[]): Analysis => ({ ...ANALYSIS, groups });

describe("GroupCards", () => {
  it("lists each group, marks the one with a public wallet, names its weakest link", () => {
    const { tracker } = renderTracker(<GroupCards />);
    const [pub, priv] = screen.getAllByRole("listitem");
    expect(within(pub!).getByText("Includes your public wallet Exchange withdrawals: anyone who knows it can tie the others to you")).toBeTruthy();
    expect(within(pub!).getByText("Weakest link: Cold storage ↔ Exchange withdrawals, 70%")).toBeTruthy();
    expect(within(pub!).getByRole("img", { name: "Bitcoin" })).toBeTruthy();
    expect(within(priv!).getByText("No public wallet in it, but to an observer these wallets are one person")).toBeTruthy();
    fireEvent.click(within(pub!).getByRole("button", { name: "2 wallets, one person" }));
    expect(tracker.openPanel).toHaveBeenCalledWith({ kind: "group", address: COLD });
  });

  it("says when no two wallets are tied, without calling them safe", () => {
    renderTracker(<GroupCards />, { analysis: with_([]) });
    expect(screen.getByText(/No two of your wallets are tied at 50% or more/).textContent).toContain("not proof of privacy");
  });
});

describe("GroupPanel", () => {
  it("lists the wallets and the links weakest first, and says which one link holds it alone", () => {
    renderTracker(<GroupPanel address={SAVINGS} />, { analysis: with_([FOUR]) });
    expect(screen.getByRole("heading", { level: 2, name: "4 wallets, one person" })).toBeTruthy();
    expect(screen.getByText(/Partly checked/)).toBeTruthy();
    const links = within(screen.getAllByRole("list")[1]!).getAllByRole("button");
    expect(links.map((b) => b.textContent?.match(/\d+%/)?.[0])).toEqual(["55%", "60%", "80%", "100%"]);
    expect(links[2]!.textContent).toContain("This link alone holds the group together: without it, Hot wallet, Savings, Phone and Cold storage are apart.");
    expect(links.filter((b) => b.textContent?.includes("alone"))).toHaveLength(1);
  });

  it("shows the group in the graph, opens a link's path and a wallet", () => {
    const { tracker } = renderTracker(<GroupPanel address={PUBLIC} />);
    fireEvent.click(screen.getByRole("button", { name: "Show in graph" }));
    expect(tracker.showGroup).toHaveBeenCalledWith(PUBLIC);
    fireEvent.click(screen.getByRole("button", { name: /Cold storage ↔ Exchange withdrawals/ }));
    expect(tracker.showPath).toHaveBeenCalledWith(ANALYSIS.groups![0]!.links[0]!.path, "Cold storage ↔ Exchange withdrawals", COLD);
    fireEvent.click(within(screen.getAllByRole("list")[0]!).getAllByRole("button")[0]!);
    expect(tracker.openWallet).toHaveBeenCalledWith(COLD);
  });

  it("shows nothing for a wallet in no group", () => {
    const { container } = renderTracker(<GroupPanel address={HOT} />, { analysis: with_([]) });
    expect(container.textContent).toBe("");
  });
});
