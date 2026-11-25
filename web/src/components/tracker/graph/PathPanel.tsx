"use client";

import { useTranslation } from "react-i18next";
import { kindOf, typeOf } from "@/lib/tracker/kinds";
import { useTracker } from "../TrackerProvider";
import { Coin, KindIcon } from "../Glyph";
import { BUTTON, ROW } from "../bits";
import { hopTexts } from "./text";

/** A path opened from a verdict or an origin: highlighted in the graph, spelled out as a list. */
export function PathPanel({ path, heading, back }: { path: string[]; heading: string; back: string }) {
  const { t, i18n } = useTranslation();
  const { graph, byAddress, nameOf, selectNode, openWallet, showPath } = useTracker();
  return (
    <div className="space-y-4">
      <div className="space-y-1 pr-8">
        <h2 className="break-words text-lg font-semibold text-foreground">{heading}</h2>
        <p className="text-sm text-muted">{t("tracker-graph:path.hops", { count: path.length - 1 })}</p>
      </div>
      <ol className="space-y-1">
        {path.map((a, i) => {
          const n = byAddress.get(a);
          const next = path[i + 1];
          return (
            <li key={`${i}:${a}`}>
              <button type="button" className={ROW} onClick={() => selectNode(a)}>
                <span className="inline-flex w-6 shrink-0 justify-center">{n?.mine ? <Coin public={n.public} size={20} /> : <KindIcon kind={kindOf(n ?? {})} size={14} />}</span>
                <span className="min-w-0">
                  <span className="block break-words text-sm text-foreground">{nameOf(a)}</span>
                  <small className="block text-xs text-muted">{typeOf(n ?? {}, t)}</small>
                </span>
              </button>
              {next === undefined ? null : (
                <div className="ml-4 flex gap-3 border-l-2 border-bitcoin py-1.5 pl-4">
                  <span className="inline-flex size-6 shrink-0 items-center justify-center rounded-full border-2 border-bitcoin bg-foreground font-mono text-xs text-background">
                    {i + 1}
                  </span>
                  <ul className="min-w-0 space-y-1 text-xs text-muted">
                    {hopTexts(graph, a, next, t, i18n.language).map((text, k) => (
                      <li key={k}>{text}</li>
                    ))}
                  </ul>
                </div>
              )}
            </li>
          );
        })}
      </ol>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={BUTTON} onClick={() => openWallet(back)}>
          {t("tracker-graph:node.back", { name: nameOf(back) })}
        </button>
        <button type="button" className={BUTTON} onClick={() => showPath([], "", back)}>
          {t("tracker-graph:path.clear")}
        </button>
      </div>
    </div>
  );
}
