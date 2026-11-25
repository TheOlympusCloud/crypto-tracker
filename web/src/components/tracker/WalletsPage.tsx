"use client";

import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import { TrackerProvider, useTracker, type PanelState, type View } from "./TrackerProvider";
import { PortfolioView } from "./portfolio/PortfolioView";
import { WalletPanel } from "./portfolio/WalletPanel";
import { GraphView } from "./graph/GraphView";
import { NodePanel } from "./graph/NodePanel";
import { PathPanel } from "./graph/PathPanel";
import { LookupBox } from "./lookup/LookupBox";
import { LookupPanel } from "./lookup/LookupPanel";
import { ManageView } from "./manage/ManageView";
import { AlertsBell } from "./manage/AlertsBell";

const FOCUS = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-bitcoin";

/** The wallet tracker: my wallets on every chain, their verdicts, origins and graph. */
export function WalletsPage() {
  return (
    <TrackerProvider>
      <Shell />
    </TrackerProvider>
  );
}

function Shell() {
  const { t } = useTranslation();
  const { view, show, panel, closePanel, status } = useTracker();
  const tabs: [View, string][] = [
    ["portfolio", t("tracker:view.portfolio")],
    ["graph", t("tracker:view.graph")],
    ["manage", t("tracker:view.manage")],
  ];
  return (
    <div className="flex-1 w-full px-4 sm:px-6 lg:px-8 pt-4 sm:pt-6 pb-12">
      <div className="mx-auto w-full max-w-7xl">
        <header className="mb-6 flex flex-wrap items-center gap-x-4 gap-y-3">
          <h1 className="text-2xl sm:text-3xl font-semibold tracking-tight text-foreground">{t("tracker:title")}</h1>
          <nav aria-label={t("tracker:view.label")} className="flex gap-1 rounded-lg border border-card-border bg-surface-inset p-1">
            {tabs.map(([v, label]) => (
              <button
                key={v}
                type="button"
                aria-current={view === v ? "page" : undefined}
                onClick={() => show(v)}
                className={`min-h-9 rounded-md px-3 text-sm transition-colors ${FOCUS} ${
                  view === v ? "bg-surface-elevated text-foreground shadow-sm" : "text-muted hover:text-foreground"
                }`}
              >
                {label}
              </button>
            ))}
          </nav>
          <div className="flex min-w-72 flex-1 items-center justify-end gap-2">
            <LookupBox />
            <AlertsBell />
          </div>
          <p
            role="status"
            className={`basis-full text-sm empty:hidden ${status.level === "error" ? "text-danger" : status.level === "busy" ? "text-bitcoin" : "text-muted"}`}
          >
            {status.text}
          </p>
        </header>
        <div className={panel ? "lg:grid lg:grid-cols-[minmax(0,1fr)_26rem] lg:gap-6" : ""}>
          <div className="min-w-0">
            {/* Every view stays mounted, so the graph keeps its scene and forms keep what was typed. */}
            <section aria-label={t("tracker:view.portfolio")} hidden={view !== "portfolio"}>
              <PortfolioView />
            </section>
            <section aria-label={t("tracker:view.graph")} hidden={view !== "graph"}>
              <GraphView active={view === "graph"} />
            </section>
            <section aria-label={t("tracker:view.manage")} hidden={view !== "manage"}>
              <ManageView />
            </section>
          </div>
          {panel && (
            <aside
              aria-label={t("tracker:panel.label")}
              className="fixed inset-x-0 bottom-0 z-30 max-h-[75vh] overflow-y-auto rounded-t-2xl border border-card-border bg-surface-elevated p-5 shadow-2xl lg:sticky lg:top-[calc(var(--header-h,4rem)+1rem)] lg:z-auto lg:max-h-[calc(100vh-var(--header-h,4rem)-2rem)] lg:self-start lg:rounded-xl lg:shadow-none"
            >
              <button
                type="button"
                onClick={closePanel}
                aria-label={t("tracker:panel.close")}
                className={`float-right -mr-1 -mt-1 inline-flex size-9 items-center justify-center rounded-md text-muted hover:text-foreground ${FOCUS}`}
              >
                <X size={18} aria-hidden />
              </button>
              <PanelContent panel={panel} />
            </aside>
          )}
        </div>
      </div>
    </div>
  );
}

function PanelContent({ panel }: { panel: PanelState }) {
  switch (panel.kind) {
    case "wallet":
      return <WalletPanel address={panel.address} />;
    case "node":
      return <NodePanel address={panel.address} />;
    case "path":
      return <PathPanel path={panel.path} heading={panel.heading} back={panel.back} />;
    default:
      return <LookupPanel panel={panel} />;
  }
}
