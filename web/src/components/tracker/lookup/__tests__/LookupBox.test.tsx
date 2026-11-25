// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { act, cleanup, fireEvent, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";

vi.mock("react-i18next", async () => (await import("@/components/tracker/__tests__/i18n-mock")).i18nMock());
vi.mock("@/components/QrScanner", () => ({
  QrScanner: ({ onResult, onClose }: { onResult: (text: string) => void; onClose: () => void }) => (
    <button
      type="button"
      onClick={() => {
        onResult("ethereum:0x28c6c06298d514db089934071355e5743bf21d60@1");
        onClose();
      }}
    >
      fake camera
    </button>
  ),
}));

import { fakeTracker, renderTracker } from "@/components/tracker/__tests__/harness";
import { TrackerContext } from "@/components/tracker/TrackerProvider";
import { LookupBox } from "../LookupBox";
import { lookupBox, useLookup } from "../useLookup";
import { LOOKUP, stubApi } from "./fixture";

const EVM = "0xd8da6bf26964af9d7eed9e03e53415d37aa96045";
const evm = { address: EVM, chain: "evm", chains: ["ethereum", "avalanche"], hint: null, needs_key: null, icon: "evm" };
const btc = { address: "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq", chain: "bitcoin", chains: [], hint: null, needs_key: null, icon: "bitcoin" };

beforeEach(() => lookupBox.set({ text: "", mark: null }));
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const box = () => screen.getByRole("textbox", { name: "Look up any address" });

describe("the lookup box", () => {
  it("shows the chain of what is typed after a pause, only while the text still matches", async () => {
    vi.useFakeTimers();
    const fetch = stubApi({ resolve: btc });
    renderTracker(<LookupBox />);
    fireEvent.change(box(), { target: { value: "bc1qar0" } });
    fireEvent.change(box(), { target: { value: btc.address } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(fetch).toHaveBeenCalledTimes(1); // the first text was replaced within the pause
    expect(JSON.parse(fetch.mock.calls[0]![1]!.body as string)).toEqual({ q: btc.address });
    expect(screen.getByRole("img", { name: "Bitcoin" })).toBeTruthy();
    fireEvent.change(box(), { target: { value: `${btc.address}x` } });
    expect(screen.queryByRole("img", { name: "Bitcoin" })).toBeNull();
  });

  it("looks up what is submitted and writes the address it names in the box", async () => {
    stubApi({ resolve: btc, [`lookup/${btc.address}`]: { ...LOOKUP, address: btc.address } });
    const { tracker } = renderTracker(<LookupBox />);
    fireEvent.change(box(), { target: { value: `  bitcoin:${btc.address}?amount=1  ` } });
    fireEvent.click(screen.getByRole("button", { name: "Look up" }));
    await waitFor(() => expect(tracker.openPanel).toHaveBeenLastCalledWith({ kind: "lookup" }));
    expect((box() as HTMLInputElement).value).toBe(btc.address);
    expect(tracker.setStatus).toHaveBeenCalledWith({ text: "", level: "" });
  });

  it("does nothing for an empty box, and shows a resolve error in the status line", async () => {
    stubApi({ resolve: new Error("that is a private key: never enter one here") });
    const { tracker } = renderTracker(<LookupBox />);
    fireEvent.submit(box());
    expect(tracker.setStatus).not.toHaveBeenCalled();
    fireEvent.change(box(), { target: { value: "5Kb8kLf9zgWQnogidDA76MzPL6TsZZY36hWXMssSzNydYXYB9KF" } });
    fireEvent.submit(box());
    await waitFor(() => expect(tracker.fail).toHaveBeenCalledWith(new Error("that is a private key: never enter one here")));
  });

  it("scans a code with am-i-exposed's scanner, without showing what it holds", async () => {
    const fetch = stubApi({ resolve: { ...evm, address: LOOKUP.address, hint: "ethereum", icon: "ethereum" }, [`lookup/${LOOKUP.address}?chains=ethereum`]: LOOKUP });
    lookupBox.set({ text: "typed before" });
    const { tracker } = renderTracker(<LookupBox />);
    fireEvent.click(screen.getByRole("button", { name: "Scan a QR code" }));
    fireEvent.click(await screen.findByRole("button", { name: "fake camera" }));
    expect((box() as HTMLInputElement).value).toBe("");
    await waitFor(() => expect(tracker.setScope).toHaveBeenCalledWith(LOOKUP));
    expect(fetch.mock.calls.map(([, init]) => init?.body)).toContain(JSON.stringify({ q: "ethereum:0x28c6c06298d514db089934071355e5743bf21d60@1" }));
    expect((box() as HTMLInputElement).value).toBe(LOOKUP.address);
    expect(screen.queryByRole("button", { name: "fake camera" })).toBeNull();
  });
});

describe("useLookup", () => {
  const hook = () => {
    const tracker = fakeTracker();
    const wrapper = ({ children }: { children: ReactNode }) => <TrackerContext.Provider value={tracker}>{children}</TrackerContext.Provider>;
    return { tracker, ...renderHook(() => useLookup(), { wrapper }) };
  };

  it("opens the address in the graph and the panel", async () => {
    stubApi({ resolve: btc, [`lookup/${btc.address}`]: LOOKUP });
    const { tracker, result } = hook();
    await act(() => result.current.lookUp(btc.address));
    expect(tracker.openPanel).toHaveBeenNthCalledWith(1, { kind: "lookup-busy", address: btc.address });
    expect(tracker.setScope).toHaveBeenCalledWith(LOOKUP);
    expect(tracker.show).toHaveBeenCalledWith("graph");
    expect(tracker.requestFocus).toHaveBeenCalledWith(btc.address);
    expect(tracker.openPanel).toHaveBeenLastCalledWith({ kind: "lookup" });
  });

  it("asks which chains for an EVM address, naming a chain this server cannot read", async () => {
    stubApi({ resolve: { ...evm, needs_key: "base" } });
    const { tracker, result } = hook();
    await act(() => result.current.lookUp(`ethereum:${EVM}@8453`));
    expect(tracker.openPanel).toHaveBeenCalledWith({ kind: "chains", address: EVM, chains: evm.chains, picked: evm.chains, missing: "base" });
    expect(tracker.setScope).not.toHaveBeenCalled();
  });

  it("reads the one chain a payment code names, or the only one there is", async () => {
    const fetch = stubApi({ resolve: { ...evm, chains: ["ethereum"] }, [`lookup/${EVM}`]: LOOKUP });
    const { tracker, result } = hook();
    await act(() => result.current.lookUp(EVM));
    expect(fetch.mock.calls[1]![0]).toBe(`/api/tracker/lookup/${EVM}`);
    expect(tracker.setScope).toHaveBeenCalledWith(LOOKUP);
  });

  it("shows the error with what to try again", async () => {
    stubApi({});
    const { tracker, result } = hook();
    await act(() => result.current.openLookup("0xabc", ["ethereum"]));
    expect(tracker.openPanel).toHaveBeenLastCalledWith({ kind: "lookup-error", address: "0xabc", chains: ["ethereum"], message: "no route lookup/0xabc?chains=ethereum" });
  });

  it("lets only the newest lookup fill the panel", async () => {
    let slow: (v: unknown) => void = () => {};
    stubApi({ "lookup/0xold": () => new Promise((done) => (slow = done)), "lookup/0xnew": LOOKUP });
    const { tracker, result } = hook();
    let first: Promise<void> = Promise.resolve();
    act(() => {
      first = result.current.openLookup("0xold");
    });
    await act(() => result.current.openLookup("0xnew"));
    await act(async () => {
      slow({ ...LOOKUP, address: "0xold" });
      await first;
    });
    expect(tracker.setScope).toHaveBeenCalledTimes(1);
    expect(tracker.setScope).toHaveBeenCalledWith(LOOKUP);
  });
});
