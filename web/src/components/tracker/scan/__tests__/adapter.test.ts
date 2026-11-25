import { describe, it, expect } from "vitest";
import { t } from "@/components/tracker/__tests__/i18n-mock";
import { gapText, toResult } from "@/lib/tracker/exposure";
import type { Exposure } from "@/lib/tracker/types";

const EVM = "0x" + "a".repeat(40);

describe("the adapter", () => {
  const scan: Exposure = {
    address: EVM, chain: "evm", chains: ["ethereum"], icon: "ethereum", score: 78, grade: "B", partial: true,
    findings: [
      {
        id: "x-exchange-exposure", severity: "high", scoreImpact: -15, confidence: "deterministic",
        params: { exchange: "Binance", count: 3, chains: "Ethereum" },
        evidence: [{ chain: "ethereum", txid: "0x1", url: "https://etherscan.io/tx/0x1" }],
        adversaryTiers: ["kyc_exchange"], temporality: "historical",
      },
    ],
    gaps: [{ id: "truncated" }, { id: "error", params: { error: "polygon: HTTP 500" } }],
    explorer_address: "https://etherscan.io/address/{}",
  };

  it("words each finding from the tracker's namespace and keeps its evidence", () => {
    const r = toResult(scan, t);
    expect([r.score, r.grade, r.partial]).toEqual([78, "B", true]);
    const [f] = r.findings;
    expect(f!.title).toBe("Dealt directly with an exchange: Binance");
    expect(f!.description).toMatch(/^3 transfers went between this address and Binance on Ethereum\./);
    expect(f!.recommendation).toMatch(/known to the exchange/);
    expect(f!.evidence[0]!.url).toBe("https://etherscan.io/tx/0x1");
    expect(f!.severity).toBe("high");
  });

  it("says why a scan is partly checked", () => {
    expect(scan.gaps.map((g) => gapText(g, t))).toEqual([
      "The address is very active: only its newest transactions were read.",
      "Not everything could be read (polygon: HTTP 500).",
    ]);
  });
});
