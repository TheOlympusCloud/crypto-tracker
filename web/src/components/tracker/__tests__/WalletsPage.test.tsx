// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, screen } from "@testing-library/react";

vi.mock("react-i18next", async () => (await import("./i18n-mock")).i18nMock());

import { renderTracker } from "./harness";
import { t } from "./i18n-mock";
import { Glyph, ChainMark } from "../Glyph";

afterEach(cleanup);

describe("tracker harness", () => {
  it("translates the tracker's namespaces", () => {
    expect(t("tracker:view.manage")).toBe("Wallets & sync");
    expect(t("tracker:sync.running", { done: 2, total: 5, what: "(labels)" })).toBe("Syncing 2/5 (labels)");
  });

  it("names a chain mark for screen readers and draws a glyph", () => {
    renderTracker(<><ChainMark chain="solana" /><Glyph shape="star" color="#f00" label="Mixer" /></>);
    expect(screen.getByRole("img", { name: "Solana" })).toBeTruthy();
    expect(screen.getByRole("img", { name: "Mixer" }).querySelector("path")?.getAttribute("fill")).toBe("#f00");
  });
});
