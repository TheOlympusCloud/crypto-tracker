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

  it("names the first funder the way the server sends it", () => {
    const funded = (id: string, params: Record<string, string>) =>
      toResult({ ...scan, findings: [{ ...scan.findings[0]!, id, params }] }, t).findings[0]!;
    const wallets = { funder: "0xfeed", wallets: "Trading", chains: "Solana" };
    expect(funded("x-funder-mine", { funder: "Main", chains: "Ethereum, Base" }).title).toBe("First funded by your wallet Main");
    const cex = funded("x-funder-exchange", { exchange: "Binance", chains: "Ethereum" });
    expect(cex.title).toBe("First funded by an exchange: Binance");
    expect(cex.description).toMatch(/^Binance sent this address its first coins on Ethereum.*one of its customers/);
    expect(funded("x-funder-shared", wallets).description).toMatch(/^0xfeed sent .* also sent coins to Trading, which you track as yours/);
    expect(funded("x-funder-hub", wallets).title).toBe("First funded by a busy address that also funded Trading");
    for (const id of ["x-funder-mine", "x-funder-exchange", "x-funder-shared", "x-funder-hub"]) {
      const f = funded(id, { ...wallets, exchange: "Binance" });
      expect([f.title, f.description, f.recommendation].join(" ")).not.toMatch(/\{\{|tracker-exposure:/);
    }
  });

  it("says when the address itself is sanctioned or frozen", () => {
    const flagged = { ...scan.findings[0]!, id: "x-flagged-address", severity: "critical" as const, scoreImpact: -20, params: { name: "Sanctioned by OFAC" }, evidence: [] };
    const [f] = toResult({ ...scan, findings: [flagged] }, t).findings;
    expect(f!.title).toBe("This address is flagged: Sanctioned by OFAC");
    expect(f!.description).toMatch(/under sanctions \(OFAC's lists\) or frozen by Tether or Circle/);
    expect(f!.recommendation).toMatch(/^Do not send money to it\./);
    expect(f!.severity).toBe("critical");
  });

  it("says why a scan is partly checked", () => {
    expect(scan.gaps.map((g) => gapText(g, t))).toEqual([
      "The address is very active: only its newest transactions were read.",
      "Not everything could be read (polygon: HTTP 500).",
    ]);
  });
});
