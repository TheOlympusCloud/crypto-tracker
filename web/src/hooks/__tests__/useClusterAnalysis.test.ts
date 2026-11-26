// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

const m = vi.hoisted(() => ({ build: vi.fn(), createApiClient: vi.fn((..._args: unknown[]) => ({})) }));

vi.mock("@/lib/analysis/cluster/build-cluster", () => ({ buildFirstDegreeCluster: m.build }));
vi.mock("@/lib/api/client", () => ({ createApiClient: m.createApiClient }));
vi.mock("@/context/NetworkContext", () => ({
  useNetwork: () => ({ config: { mempoolBaseUrl: "https://mempool.space/api" }, isUmbrel: false, isCustomApi: false }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (k: string, o?: { defaultValue?: string }) => o?.defaultValue ?? k }),
}));

import { useClusterAnalysis } from "../useClusterAnalysis";
import { setChain } from "@/lib/bitcoin/chain-profile";

const ADDR = "bc1qtarget";

beforeEach(() => { m.build.mockReset(); });

describe("useClusterAnalysis", () => {
  it("goes analyzing -> complete with progress along the way", async () => {
    const cluster = { addresses: [ADDR] };
    let report!: (p: unknown) => void;
    let finish!: () => void;
    m.build.mockImplementation((_a, _t, _api, _s, onProgress: (p: unknown) => void) => {
      report = onProgress;
      return new Promise((r) => { finish = () => r(cluster); });
    });
    const { result } = renderHook(() => useClusterAnalysis());

    let done!: Promise<void>;
    act(() => { done = result.current.analyze(ADDR, []); });
    expect(result.current.phase).toBe("analyzing");

    act(() => report({ phase: "fetching", current: 1, total: 2 }));
    expect(result.current.progress).toEqual({ phase: "fetching", current: 1, total: 2 });

    await act(async () => { finish(); await done; });
    expect(result.current.phase).toBe("complete");
    expect(result.current.result).toBe(cluster);
    expect(result.current.progress).toBeNull();
  });

  it("maps a failure to an error message", async () => {
    m.build.mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useClusterAnalysis());
    await act(async () => { await result.current.analyze(ADDR, []); });
    expect(result.current.phase).toBe("error");
    expect(result.current.error).toEqual(expect.any(String));
  });

  it("reset() aborts the in-flight run and ignores its late result", async () => {
    let finish!: () => void;
    let signal!: AbortSignal;
    m.build.mockImplementation((_a, _t, _api, s: AbortSignal) => {
      signal = s;
      return new Promise((r) => { finish = () => r({}); });
    });
    const { result } = renderHook(() => useClusterAnalysis());
    let done!: Promise<void>;
    act(() => { done = result.current.analyze(ADDR, []); });
    act(() => result.current.reset());
    expect(signal.aborted).toBe(true);

    await act(async () => { finish(); await done; });
    expect(result.current.phase).toBe("idle");
    expect(result.current.result).toBeNull();
  });
});

describe("useClusterAnalysis on another chain (crypto-tracker)", () => {
  it("builds the cluster from the scanned chain's backend, not mempool.space", async () => {
    m.build.mockResolvedValue({ addresses: [ADDR] });
    setChain("litecoin");
    try {
      const { result } = renderHook(() => useClusterAnalysis());
      await act(async () => { await result.current.analyze("ltc1qtarget", []); });
    } finally {
      setChain("bitcoin");
    }
    expect(m.createApiClient.mock.calls.at(-1)![0]).toEqual({ mempoolBaseUrl: "/api/tracker/utxo/litecoin" });
  });
});
