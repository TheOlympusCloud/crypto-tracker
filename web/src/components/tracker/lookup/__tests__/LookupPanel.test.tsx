// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";

vi.mock("react-i18next", async () => (await import("@/components/tracker/__tests__/i18n-mock")).i18nMock());

import { renderTracker } from "@/components/tracker/__tests__/harness";
import { LookupPanel } from "../LookupPanel";
import { LOOKUP, stubApi } from "./fixture";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const lookup = (over = {}) => renderTracker(<LookupPanel panel={{ kind: "lookup" }} />, { scope: LOOKUP, ...over });

describe("the lookup panel", () => {
  it("shows a stranger's address: state, value, risk, holdings, links and gaps", () => {
    lookup();
    expect(screen.getByRole("heading", { level: 2, name: "Binance 14" })).toBeTruthy();
    expect(screen.getByText("not yours")).toBeTruthy();
    expect(screen.getAllByText("$4,500")[0]).toBeTruthy();
    expect(screen.getByRole("img", { name: "Ethereum" })).toBeTruthy();
    expect(screen.getByText("Be careful")).toBeTruthy();
    // The reason is in the box; only the other reasons are listed under it.
    expect(screen.getAllByText("An exchange's hot wallet.")).toHaveLength(1);
    expect(screen.getByText("Active since 2017.")).toBeTruthy();

    expect(screen.getByText("ETH · Ethereum")).toBeTruthy();
    expect(screen.getByText("PEPE · Ethereum")).toBeTruthy();
    expect(screen.getByText("no price")).toBeTruthy();
    expect(screen.getByText("1 fake token · worth 0")).toBeTruthy();

    expect(screen.getByRole("heading", { name: "Links to your wallets" })).toBeTruthy();
    const mine = screen.getByRole("button", { name: /Hot wallet \(private\)/ });
    expect(within(mine).getByText("60%")).toBeTruthy();
    expect(within(mine).getByText("out 0.5 ETH · 3 transfers · 2023-11-14 to 2024-03-09")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Counterparties (5)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Tornado Cash Router · Mixer/ })).toBeTruthy();

    expect(screen.getByText(/Only direct dealings with your wallets show here/)).toBeTruthy();
    expect(screen.getByText("4 quieter counterparties not listed.")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Explorer/ }).getAttribute("href")).toBe("https://etherscan.io/address/0x28c6c06298d514db089934071355e5743bf21d60");
    // Off Bitcoin, the tracker's own privacy scan
    expect(screen.getByRole("link", { name: "Privacy score" }).getAttribute("href")).toMatch(/^\/tracker\/scan\/#addr=0x28c6c06298d514db089934071355e5743bf21d60/);
  });

  it("says when nothing ties it to my wallets, unsure when not everything was read", () => {
    lookup({ scope: { ...LOOKUP, connections: [], more: 0, assets: [], errors: ["timeout"] } });
    expect(screen.getByText("No direct dealing with your wallets found")).toBeTruthy();
    expect(screen.getByText("Nothing found.")).toBeTruthy();
    expect(screen.getByText("None found.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Counterparties (0)" })).toBeTruthy();
  });

  it("tracks it as a private wallet of mine, syncs it and redraws without reading it again", async () => {
    const fetch = stubApi({ wallets: { ok: true } });
    const { tracker } = lookup();
    fireEvent.click(screen.getByRole("button", { name: "Track as mine" }));
    await waitFor(() => expect(tracker.refresh).toHaveBeenCalled());
    expect(JSON.parse(fetch.mock.calls[0]![1]!.body as string)).toEqual({ address: LOOKUP.address, name: "", public: false });
    expect(tracker.startSync).toHaveBeenCalledWith([LOOKUP.address]);
    expect(tracker.setScope).toHaveBeenCalledWith({ ...LOOKUP, mine: 1, public: 0, watched: false });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("watches it, and stops watching it", async () => {
    const fetch = stubApi({ watch: { ok: true }, [`DELETE watch/${LOOKUP.address}`]: { ok: true } });
    const first = lookup();
    fireEvent.click(screen.getByRole("button", { name: "Watch" }));
    await waitFor(() => expect(first.tracker.setScope).toHaveBeenCalledWith({ ...LOOKUP, watched: true }));
    expect(fetch.mock.calls[0]![0]).toBe("/api/tracker/watch");
    cleanup();

    const second = lookup({ scope: { ...LOOKUP, watched: true } });
    expect(screen.getByText("watched")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Stop watching" }));
    await waitFor(() => expect(second.tracker.setScope).toHaveBeenCalledWith({ ...LOOKUP, watched: false }));
    expect(fetch.mock.calls[1]![1]!.method).toBe("DELETE");
  });

  it("puts an error in the status line", async () => {
    stubApi({});
    const { tracker } = lookup();
    fireEvent.click(screen.getByRole("button", { name: "Watch" }));
    await waitFor(() => expect(tracker.fail).toHaveBeenCalled());
    expect(tracker.setScope).not.toHaveBeenCalled();
  });

  it("labels it and opens a counterparty through the node panel", () => {
    const { tracker } = lookup();
    fireEvent.click(screen.getByRole("button", { name: "Edit label" }));
    expect(tracker.selectNode).toHaveBeenCalledWith(LOOKUP.address);
    fireEvent.click(screen.getByRole("button", { name: /Tornado Cash Router/ }));
    expect(tracker.selectNode).toHaveBeenCalledWith("0x722122df12d4e14e13ac3b6895a86e84145b6967");
  });

  it("asks again which EVM chains to read, with the ones read checked", async () => {
    stubApi({ resolve: { address: LOOKUP.address, chain: "evm", chains: ["ethereum", "avalanche"], hint: null, needs_key: null, icon: "evm" } });
    const { tracker } = lookup();
    fireEvent.click(screen.getByRole("button", { name: "Chains" }));
    await waitFor(() =>
      expect(tracker.openPanel).toHaveBeenCalledWith({ kind: "chains", address: LOOKUP.address, chains: ["ethereum", "avalanche"], picked: ["ethereum"], missing: "" }),
    );
  });

  it("opens the verdict of one of mine, and offers the privacy score for Bitcoin", () => {
    const address = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq";
    const { tracker } = lookup({ scope: { ...LOOKUP, address, mine: 1, public: 0, name: "Cold storage", chain: "bitcoin", icon: "bitcoin", connections: [], more: 0 } });
    expect(screen.getByText("private")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Track as mine" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Chains" })).toBeNull();
    expect(screen.getByRole("heading", { name: "Your other wallets it dealt with" })).toBeTruthy();
    expect(screen.queryByText(/Only direct dealings/)).toBeNull();
    expect(screen.getByRole("link", { name: "Privacy score" }).getAttribute("href")).toBe(`/#addr=${address}`);
    fireEvent.click(screen.getByRole("button", { name: "Verdict and origin" }));
    expect(tracker.openWallet).toHaveBeenCalledWith(address);
  });
});

describe("the other lookup panels", () => {
  it("shows the wait", () => {
    renderTracker(<LookupPanel panel={{ kind: "lookup-busy", address: "bc1qxyz" }} />);
    expect(screen.getByRole("heading", { name: "Looking up" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toMatch(/Reading it from the indexers/);
  });

  it("shows the error and tries the same chains again", async () => {
    const fetch = stubApi({ "lookup/0xabc?chains=ethereum": LOOKUP });
    const { tracker } = renderTracker(<LookupPanel panel={{ kind: "lookup-error", address: "0xabc", chains: ["ethereum"], message: "the indexer did not answer" }} />);
    expect(screen.getByRole("heading", { name: "Could not look it up" })).toBeTruthy();
    expect(screen.getByText("the indexer did not answer")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(tracker.openPanel).toHaveBeenCalledWith({ kind: "lookup-busy", address: "0xabc" });
    await waitFor(() => expect(tracker.openPanel).toHaveBeenLastCalledWith({ kind: "lookup" }));
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("asks which chains, names the one this server cannot read, and reads the ones left checked", async () => {
    const fetch = stubApi({ "lookup/0xabc?chains=avalanche": LOOKUP });
    const { tracker } = renderTracker(<LookupPanel panel={{ kind: "chains", address: "0xabc", chains: ["ethereum", "avalanche"], picked: ["ethereum", "avalanche"], missing: "base" }} />);
    expect(screen.getByRole("heading", { name: "Which chains?" })).toBeTruthy();
    expect(screen.getByText(/This code is for Base, which this server does not read yet\. Needs a free Blockscout key/)).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: /Ethereum/ }));
    fireEvent.click(screen.getByRole("button", { name: "Look up" }));
    await waitFor(() => expect(tracker.setScope).toHaveBeenCalledWith(LOOKUP));
    expect(fetch.mock.calls[0]![0]).toBe("/api/tracker/lookup/0xabc?chains=avalanche");
  });
});
