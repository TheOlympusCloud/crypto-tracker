import { describe, it, expect } from "vitest";
import { calculateScore } from "@/lib/scoring/score";
import type { Finding } from "@/lib/types";
import { accountChain, accountScanHref, exposurePath, readScanHash, scanHref } from "../exposure";
import cases from "./score-cases.json";

const EVM = "0x" + "a".repeat(40);
const SOL = "vines1vzrYbzLMRdu58ou5XTby4qAqVRLmqo36NKPTg";
const TRX = "TLa2f6VPqDgRE67v1736s7bJ8Ray5wYjU7";
const BCH = "qpc2hdexmqwy06jzvdxddkazyp500kqf9vvmvgxnqm";

describe("the server's score", () => {
  // The same cases as tests/test_exposure.py: both ports of score.ts must agree.
  it.each(cases)("is am-i-exposed's address score: $name", ({ findings, score, grade, order }) => {
    const got = calculateScore(findings as unknown as Finding[], "address");
    expect([got.score, got.grade]).toEqual([score, grade]);
    expect(got.findings.map((f) => f.id)).toEqual(order);
  });
});

describe("account addresses in am-i-exposed's input", () => {
  it("knows EVM, Solana and Tron by their format, and nothing else", () => {
    expect(accountChain(EVM)).toBe("evm");
    expect(accountChain(SOL)).toBe("solana");
    expect(accountChain(TRX)).toBe("tron");
    for (const other of ["1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "LaMT348PWRnrqeeWArpwQPbuanpXDZGEUz", "DH5yaieqoZN36fDVciNyRueRGvGLR3mr7L", "0x123", "hello", ""]) {
      expect(accountChain(other), other).toBeNull();
    }
    expect(accountScanHref(SOL)).toBe(`/tracker/scan/#addr=${SOL}`);
    expect(accountScanHref("hello")).toBeNull();
  });

  it("sends Privacy score to the scan of each chain", () => {
    expect(scanHref("bc1q x", "bitcoin")).toBe("/#addr=bc1q%20x");
    expect(scanHref(EVM, "evm")).toBe(`/tracker/scan/#addr=${EVM}`);
    expect(scanHref(EVM, "polygon")).toBe(`/tracker/scan/#addr=${EVM}&chains=polygon`); // an EVM address read on one chain
    expect(scanHref(TRX, "tron")).toBe(`/tracker/scan/#addr=${TRX}`);
    // Litecoin, Dogecoin and Bitcoin Cash: am-i-exposed's scan with the chain's profile
    expect(scanHref("LaMT348PWRnrqeeWArpwQPbuanpXDZGEUz", "litecoin")).toBe("/#addr=LaMT348PWRnrqeeWArpwQPbuanpXDZGEUz&chain=litecoin");
    expect(scanHref(`bitcoincash:${BCH}`, "bitcoincash")).toBe(`/#addr=${BCH}&chain=bitcoincash`);
    expect(scanHref("DPtotDSjTaSvNQxQvXWQd3wgbdBteez78z", "dogecoin")).toBe("/#addr=DPtotDSjTaSvNQxQvXWQd3wgbdBteez78z&chain=dogecoin");
    expect(scanHref(EVM, "")).toBeNull();
    expect(readScanHash(`#addr=${EVM}&chains=ethereum,base`)).toEqual({ address: EVM, chains: ["ethereum", "base"] });
    expect(readScanHash("")).toEqual({ address: "", chains: [] });
    expect(exposurePath(EVM, ["ethereum"])).toBe(`exposure/${EVM}?chains=ethereum`);
    expect(exposurePath(SOL, [])).toBe(`exposure/${SOL}`);
  });
});
