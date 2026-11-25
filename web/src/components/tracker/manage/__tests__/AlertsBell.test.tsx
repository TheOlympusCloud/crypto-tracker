// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";

vi.mock("react-i18next", async () => (await import("../../__tests__/i18n-mock")).i18nMock());
vi.mock("@/lib/tracker/api", () => ({ api: vi.fn(async () => ({ ok: true })) }));

import { api } from "@/lib/tracker/api";
import type { Alerts } from "@/lib/tracker/types";
import { ANALYSIS, renderTracker } from "../../__tests__/harness";
import { AlertsBell } from "../AlertsBell";

const apiMock = vi.mocked(api);
const COLD = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq";
const withAlerts = (alerts: Alerts) => ({ analysis: { ...ANALYSIS, alerts } });
const bell = () => screen.getByRole("button", { name: /^Alerts, / });

beforeEach(() => apiMock.mockClear());
afterEach(cleanup);

describe("AlertsBell", () => {
  it("stays away until the API reports an alert", () => {
    const { container } = renderTracker(<AlertsBell />, withAlerts({ unread: 0, items: [] }));
    expect(container.innerHTML).toBe("");
  });

  it("opens the list, marks the unread alerts seen and reads the data again", async () => {
    const { tracker } = renderTracker(<AlertsBell />);
    expect(bell().getAttribute("aria-expanded")).toBe("false");
    expect(bell().textContent).toContain("1");
    const list = document.getElementById(bell().getAttribute("aria-controls")!)!;
    expect(list.hidden).toBe(true);

    fireEvent.click(bell());
    expect(bell().getAttribute("aria-expanded")).toBe("true");
    expect(list.hidden).toBe(false);
    expect(apiMock).toHaveBeenCalledWith("alerts/seen", {});
    await waitFor(() => expect(tracker.refresh).toHaveBeenCalled());

    fireEvent.click(bell());
    expect(list.hidden).toBe(true);
    expect(apiMock).toHaveBeenCalledTimes(1); // closing marks nothing
  });

  it("shows each alert's text and date, and opens its wallet", () => {
    const { tracker } = renderTracker(<AlertsBell />);
    fireEvent.click(bell());
    const alert = screen.getByRole("button", { name: /Cold storage is now linked/ });
    expect(alert.textContent).toContain("2026-11-23");
    fireEvent.click(alert);
    expect(tracker.openWallet).toHaveBeenCalledWith(COLD);
  });

  it("marks nothing when all alerts are read, and disables one about a wallet that is gone", () => {
    renderTracker(<AlertsBell />, withAlerts({ unread: 0, items: [{ time: 1795435550, text: "Old news", address: "bc1qgone", seen: true }] }));
    expect(bell().getAttribute("aria-label")).toBe("Alerts, 0 unread");
    fireEvent.click(bell());
    expect(apiMock).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: /Old news/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("caps the unread count at 99+", () => {
    const items = Array.from({ length: 3 }, (_, i) => ({ time: 1795435550 + i, text: `Alert ${i}`, address: COLD, seen: false }));
    renderTracker(<AlertsBell />, withAlerts({ unread: 120, items }));
    expect(bell().getAttribute("aria-label")).toBe("Alerts, 120 unread");
    expect(bell().textContent).toContain("99+");
  });
});
