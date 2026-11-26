import { describe, it, expect } from "vitest";
import { DARK_PALETTE } from "@/lib/palette";
import { LINKS, linkColor, linkDash } from "../kinds";

describe("guesses in the graph", () => {
  const kinds = Object.keys(LINKS);

  it("draw every kind the server stores", () => {
    expect(kinds).toEqual(expect.arrayContaining(["common-input", "change", "bridge", "bridge-record", "co-signer", "deposit-address", "exchange-flow", "fee-payer", "name", "swap"]));
  });

  it("give each kind a colour and a dash pattern of its own, so the legend tells them apart", () => {
    const dashes = kinds.map((k) => linkDash(k).join(","));
    expect(dashes).not.toContain(linkDash("no-such-kind").join(","));
    expect(new Set(dashes).size).toBe(kinds.length);
    expect(new Set(kinds.map((k) => linkColor(k, DARK_PALETTE))).size).toBe(kinds.length);
  });
});
