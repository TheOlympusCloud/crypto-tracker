import { describe, it, expect } from "vitest";
import { t } from "../../__tests__/i18n-mock";
import { GRAPH } from "../../__tests__/harness";
import type { Graph } from "@/lib/tracker/types";
import { explorerUrl, hopTexts, namedBy } from "../text";

const COLD = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq";
const STRANGER = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa";

describe("hopTexts", () => {
  it("spells out the transfers both ways, then the guesses", () => {
    expect(hopTexts(GRAPH, COLD, STRANGER, t, "en")).toEqual([
      "received 0.1 BTC on bitcoin (1 transfer, 2026-10-26)",
      "guess: common-input, 90% confidence (1 transaction)",
    ]);
    expect(hopTexts(GRAPH, STRANGER, COLD, t, "en")[0]).toBe("sent 0.1 BTC on bitcoin (1 transfer, 2026-10-26)");
  });

  it("gives a date range, counts and the CoinJoin caveat", () => {
    const graph: Graph = {
      nodes: [],
      edges: [{ chain: "bitcoin", src: "a", dst: "b", asset: "BTC", token: "", count: 3, amount: 1.23456789, first: 1792152350, last: 1793016350, coinjoin: 1 }],
      links: [{ a: "b", b: "a", kind: "change", confidence: 0.456, count: 2 }],
    };
    expect(hopTexts(graph, "a", "b", t, "en")).toEqual([
      "sent 1.2346 BTC on bitcoin (3 transfers, 2026-10-16 to 2026-10-26) · inside a CoinJoin, which does not show who paid whom",
      "guess: change, 46% confidence (2 transactions)",
    ]);
  });

  it("is empty between addresses with nothing between them", () => {
    expect(hopTexts(GRAPH, COLD, "0xd8da6bf26964af9d7eed9e03e53415d37aa96045", t, "en")).toEqual([]);
  });
});

describe("namedBy", () => {
  it("says where a label comes from, and how sure a guess is", () => {
    expect(namedBy({ label: "Binance 14", label_source: "dataset" }, t)).toBe("an open label set");
    expect(namedBy({ label: "Mine", label_source: "user" }, t)).toBe("you");
    expect(namedBy({ label: "Router?", label_source: "heuristic", label_confidence: 0.62 }, t)).toBe("its behaviour: a guess, 62% sure");
    expect(namedBy({ label_source: "dataset" }, t)).toBe("");
  });
});

describe("explorerUrl", () => {
  it("fills the address in, without the Bitcoin Cash prefix", () => {
    expect(explorerUrl("https://mempool.space/address/{}", COLD)).toBe(`https://mempool.space/address/${COLD}`);
    expect(explorerUrl("https://bch.example/address/{}", "bitcoincash:qq07")).toBe("https://bch.example/address/qq07");
  });
});
