"use client";

import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown } from "lucide-react";
import { usePalette } from "@/hooks/usePalette";
import { COUNTERPARTY_KINDS, LINKS, PATH, kindLabelKey, linkDash } from "@/lib/tracker/kinds";
import { KindIcon, LineSample } from "../Glyph";

const Item = ({ mark, children }: { mark: ReactNode; children: ReactNode }) => (
  <li className="grid grid-cols-[30px_1fr] items-center gap-2">
    <span className="justify-self-center">{mark}</span>
    <span>{children}</span>
  </li>
);

const Heading = ({ children }: { children: ReactNode }) => <h3 className="eyebrow mb-2 mt-3">{children}</h3>;

/** What the graph's glyphs and lines mean. Controlled: the graph closes it when the side panel needs its room. */
export function Legend({ open, onToggle }: { open: boolean; onToggle: (open: boolean) => void }) {
  const { t } = useTranslation();
  const p = usePalette();
  return (
    <details
      open={open}
      onToggle={(e) => onToggle(e.currentTarget.open)}
      className="group absolute right-3 top-3 z-10 max-h-[calc(100%-1.5rem)] w-[min(17rem,calc(100%-1.5rem))] overflow-y-auto rounded-xl border border-card-border bg-card-bg/95 text-sm shadow-lg backdrop-blur"
    >
      <summary className="flex min-h-10 cursor-pointer list-none items-center justify-between gap-3 px-3 font-medium text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-bitcoin [&::-webkit-details-marker]:hidden">
        {t("tracker-graph:legend.title")}
        <ChevronDown size={16} aria-hidden className="text-muted transition-transform group-open:rotate-180" />
      </summary>
      <div className="px-3 pb-3 text-muted">
        <Heading>{t("tracker-graph:legend.wallets")}</Heading>
        <ul className="grid gap-2">
          <Item mark={<KindIcon kind="mine" size={22} />}>{t("tracker-graph:legend.private")}</Item>
          <Item mark={<KindIcon kind="public" size={22} />}>{t("tracker-graph:legend.public")}</Item>
        </ul>
        <Heading>{t("tracker-graph:legend.lines")}</Heading>
        <ul className="grid gap-2">
          <Item mark={<LineSample color={PATH} dash={[]} width={3.5} />}>{t("tracker-graph:legend.path")}</Item>
          <Item mark={<LineSample color={p.muted} dash={[]} width={1} />}>{t("tracker-graph:legend.transfers")}</Item>
          {Object.entries(LINKS).map(([kind, color]) => (
            <Item key={kind} mark={<LineSample color={color} dash={linkDash(kind)} />}>
              {t("tracker-graph:legend.guess", { kind })}
            </Item>
          ))}
        </ul>
        <Heading>{t("tracker-graph:legend.counterparties")}</Heading>
        <ul className="grid gap-2">
          {COUNTERPARTY_KINDS.map((kind) => (
            <Item key={kind} mark={<KindIcon kind={kind} size={14} />}>
              {t(kindLabelKey(kind))}
            </Item>
          ))}
        </ul>
        <p className="mt-3 text-xs leading-relaxed">{t("tracker-graph:legend.hint")}</p>
      </div>
    </details>
  );
}
