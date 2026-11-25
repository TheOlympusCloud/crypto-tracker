// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";

vi.mock("react-i18next", async () => (await import("../../__tests__/i18n-mock")).i18nMock());

import { ANALYSIS, renderTracker } from "../../__tests__/harness";
import { PathPanel } from "../PathPanel";

const COLD = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq";
const STRANGER = "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy";
const PUBLIC = "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh";
const path = ANALYSIS.verdicts.find((v) => v.address === COLD)!.links[0]!.path;

afterEach(cleanup);

describe("PathPanel", () => {
  it("lists the path's hops with what passed between them", () => {
    renderTracker(<PathPanel path={path} heading="Cold storage ↔ Exchange withdrawals" back={COLD} />);
    expect(path).toEqual([COLD, STRANGER, PUBLIC]);
    expect(screen.getByRole("heading", { name: "Cold storage ↔ Exchange withdrawals" })).toBeTruthy();
    expect(screen.getByText("2 hops, drawn as the fat orange line with numbered hops.")).toBeTruthy();
    const items = [...document.querySelectorAll("ol > li")];
    expect(items).toHaveLength(3);
    expect(items[0]?.textContent).toContain("Cold storage");
    expect(items[0]?.textContent).toContain("My wallet, private");
    expect(items[0]?.textContent).toContain("received 0.39 BTC on bitcoin (1 transfer, 2026-10-17)");
    expect(items[1]?.textContent).toContain("Unknown address");
    expect(items[1]?.textContent).toContain("received 0.4 BTC on bitcoin");
    expect(items[2]?.textContent).toContain("Exchange withdrawals");
    expect(items[2]?.textContent).toContain("My wallet, public");
  });

  it("opens a hop's address, goes back to the wallet, clears the highlight", () => {
    const { tracker } = renderTracker(<PathPanel path={path} heading="Origin" back={COLD} />);
    fireEvent.click(screen.getByRole("button", { name: /Unknown address/ }));
    expect(tracker.selectNode).toHaveBeenCalledWith(STRANGER);
    fireEvent.click(screen.getByRole("button", { name: "Back to Cold storage" }));
    expect(tracker.openWallet).toHaveBeenCalledWith(COLD);
    fireEvent.click(screen.getByRole("button", { name: "Clear highlight" }));
    expect(tracker.showPath).toHaveBeenCalledWith([], "", COLD);
  });

  it("counts a single hop", () => {
    renderTracker(<PathPanel path={[COLD, STRANGER]} heading="Origin" back={COLD} />);
    expect(screen.getByText("1 hop, drawn as the fat orange line with numbered hops.")).toBeTruthy();
  });
});
