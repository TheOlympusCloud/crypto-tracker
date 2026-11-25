"use client";

import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Bell, ChevronDown } from "lucide-react";
import { api } from "@/lib/tracker/api";
import { day } from "@/lib/tracker/format";
import { useTracker } from "../TrackerProvider";
import { VerdictIcon } from "../Glyph";
import { BUTTON, ROW } from "../bits";

/** The alerts: shown once the API reports any. Opening the list marks the unread ones seen. */
export function AlertsBell() {
  const { t } = useTranslation();
  const { analysis, byAddress, openWallet, refresh, fail, nameOf } = useTracker();
  const [open, setOpen] = useState(false);
  const list = useId();
  const { unread, items } = analysis.alerts;
  if (!items.length) return null;
  const toggle = () => {
    setOpen(!open);
    if (!open && unread) api("alerts/seen", {}).then(refresh).catch(fail);
  };
  return (
    <div className="relative">
      <button
        type="button"
        className={BUTTON}
        aria-expanded={open}
        aria-controls={list}
        aria-label={t("tracker-manage:alerts.label", { count: unread })}
        onClick={toggle}
      >
        <Bell size={16} aria-hidden />
        <span className="hidden sm:inline">{t("tracker-manage:alerts.title")}</span>
        {unread ? (
          <span className="min-w-5 rounded-full bg-bitcoin px-1.5 text-center font-mono text-[11px] font-semibold leading-5 text-black">{unread > 99 ? "99+" : unread}</span>
        ) : (
          <ChevronDown size={14} aria-hidden className={`text-muted transition-transform ${open ? "rotate-180" : ""}`} />
        )}
      </button>
      <div
        id={list}
        hidden={!open}
        className="absolute right-0 top-full z-40 mt-2 w-[min(24rem,calc(100vw-2rem))] rounded-xl border border-card-border bg-surface-float p-3 shadow-(--shadow-pop)"
      >
        <h2 className="eyebrow mb-2 px-2">{t("tracker-manage:alerts.title")}</h2>
        <ul className="max-h-96 overflow-y-auto">
          {items.map((a, i) => (
            <li key={`${a.time}-${i}`}>
              <button type="button" className={`${ROW} items-start disabled:opacity-60 disabled:hover:bg-transparent`} disabled={!byAddress.has(a.address)} onClick={() => openWallet(a.address)}>
                <VerdictIcon level="bad" size={16} />
                <span className="min-w-0 text-sm text-foreground">
                  {a.kind === "grade" && a.params ? t("tracker-manage:alerts.grade", { name: nameOf(a.address), ...a.params }) : a.text}
                  <small className="block font-mono text-xs text-muted">{day(a.time)}</small>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
