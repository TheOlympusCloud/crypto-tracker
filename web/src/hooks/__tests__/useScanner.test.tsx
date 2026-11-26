// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

const m = vi.hoisted(() => ({
  analyze: vi.fn(),
  walletReset: vi.fn(),
  setHash: vi.fn(),
  skip: { current: false },
}));

vi.mock("@/hooks/useAnalysis", () => ({
  useAnalysis: () => ({ phase: "idle", query: null, inputType: null, result: null, preSendResult: null, error: null, analyze: m.analyze, reset: vi.fn() }),
}));
vi.mock("@/hooks/useWalletAnalysis", () => ({ useWalletAnalysis: () => ({ phase: "idle", reset: m.walletReset, analyze: vi.fn() }) }));
vi.mock("@/hooks/useRecentScans", () => ({ useRecentScans: () => ({ addScan: vi.fn() }) }));
vi.mock("@/hooks/useBookmarks", () => ({ useBookmarks: () => ({}) }));
vi.mock("@/hooks/useKeyboardNav", () => ({ useKeyboardNav: vi.fn() }));
vi.mock("@/hooks/useHashRouting", () => ({
  useHashRouting: () => ({ pendingHash: null, dismissPendingHash: vi.fn(), skipNextHashChangeRef: m.skip }),
}));
vi.mock("@/lib/hash-nav", () => ({ setHash: m.setHash }));
vi.mock("@/components/wallet/XpubPrivacyWarning", () => ({ isXpubPrivacyAcked: () => true }));
vi.mock("@/context/NetworkContext", () => ({
  useNetwork: () => ({ network: "mainnet", customApiUrl: null, isUmbrel: false, config: { mempoolBaseUrl: "https://mempool.space/api" } }),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k }) }));

import { useScanner } from "../useScanner";
import { setChain } from "@/lib/bitcoin/chain-profile";

const TXID = "b".repeat(64);

beforeEach(() => { vi.clearAllMocks(); m.skip.current = false; window.location.hash = ""; });

describe("useScanner.handleBroadcastSuccess", () => {
  it("sets the tx hash and starts an awaitIndexing scan", () => {
    const { result } = renderHook(() => useScanner());
    act(() => { result.current.handleBroadcastSuccess(TXID); });
    expect(m.setHash).toHaveBeenCalledWith(`tx=${TXID}`);
    expect(m.skip.current).toBe(true);
    expect(m.walletReset).toHaveBeenCalled();
    expect(m.analyze).toHaveBeenCalledWith(TXID, { awaitIndexing: true });
  });

  it("does not arm the hashchange skip when the hash is already the tx", () => {
    window.location.hash = `#tx=${TXID}`;
    const { result } = renderHook(() => useScanner());
    act(() => { result.current.handleBroadcastSuccess(TXID); });
    expect(m.skip.current).toBe(false);
    expect(m.analyze).toHaveBeenCalledWith(TXID, { awaitIndexing: true });
  });
});

describe("useScanner.handleSubmit on other chains (crypto-tracker)", () => {
  afterEach(() => setChain("bitcoin"));

  it("puts the chain in the hash: an address's own, a txid the one shown", () => {
    const LTC = "ltc1q4rdwpcksqmsv6nxxze6ls8f0e3a4z3u3z43kjr";
    const { result } = renderHook(() => useScanner());
    act(() => { result.current.handleSubmit(LTC); });
    expect(m.setHash).toHaveBeenLastCalledWith(`addr=${LTC}&chain=litecoin`);
    act(() => { result.current.handleSubmit(TXID); });
    expect(m.setHash).toHaveBeenLastCalledWith(`tx=${TXID}`);
    setChain("dogecoin"); // a Dogecoin result is shown: its transactions are Dogecoin's
    act(() => { result.current.handleSubmit(TXID); });
    expect(m.setHash).toHaveBeenLastCalledWith(`tx=${TXID}&chain=dogecoin`);
  });
});
