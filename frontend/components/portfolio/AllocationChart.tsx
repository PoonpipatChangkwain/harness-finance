"use client";

/**
 * Allocation donut (Recharts) by symbol / sector / asset type / currency with a table
 * legend (ui-spec §6). Slices come straight from `analytics.weights` / `analytics.exposure`.
 */

import { clsx } from "clsx";
import { useState } from "react";
import { Cell, Pie, PieChart, Tooltip } from "recharts";

import { formatPct } from "@/lib/portfolio-format";
import type { PortfolioAnalytics } from "@/lib/portfolio-api";

import { ProvValue } from "./ProvValue";
import { useT } from "@/lib/i18n";

/** Categorical palette readable on both themes. */
export const PALETTE = ["#4ea1ff", "#3fb97f", "#e6a23c", "#c678dd", "#f06a6a", "#56b6c2", "#d19a66", "#98c379", "#e06c9f", "#8a95a3"];
export const CASH_COLOR = "#6f7b89";

export type AllocationBy = "symbol" | "sector" | "asset_type" | "currency";
const LABELS: Record<AllocationBy, string> = { symbol: "Symbol", sector: "Sector", asset_type: "Asset type", currency: "Currency" };

export interface Slice {
  name: string;
  value: number;
  color: string;
}

export function allocationSlices(a: PortfolioAnalytics, by: AllocationBy): { slices: Slice[]; provenanceId: string | null } {
  const source = by === "symbol" ? a.weights : (a.exposure[by] ?? {});
  const pid = by === "symbol" ? a.weights_provenance_id : (a.exposure_provenance[by] ?? null);
  const names = new Map(a.holdings.map((h) => [h.symbol, h.display]));
  let i = 0;
  const slices = Object.entries(source)
    .filter(([, v]) => v > 0)
    .sort((x, y) => y[1] - x[1])
    .map(([k, v]) => ({
      name: by === "symbol" ? (names.get(k) ?? k) : k,
      value: v,
      color: k === "CASH" ? CASH_COLOR : PALETTE[i++ % PALETTE.length]!,
    }));
  return { slices, provenanceId: pid };
}

export function Donut({ slices, size = 180 }: { slices: Slice[]; size?: number }) {
  return (
    <PieChart width={size} height={size}>
      <Pie data={slices} dataKey="value" nameKey="name" innerRadius={size * 0.3} outerRadius={size * 0.46} paddingAngle={1} stroke="none" isAnimationActive={false}>
        {slices.map((s) => (
          <Cell key={s.name} fill={s.color} />
        ))}
      </Pie>
      <Tooltip
        formatter={(v) => formatPct(Number(v))}
        contentStyle={{ background: "var(--fh-bg-layer-1)", border: "1px solid var(--fh-border-strong)", fontSize: 12 }}
        itemStyle={{ color: "var(--fh-text)" }}
      />
    </PieChart>
  );
}

export function AllocationChart({ analytics }: { analytics: PortfolioAnalytics }) {
  const t = useT();
  const [by, setBy] = useState<AllocationBy>("symbol");
  const { slices, provenanceId } = allocationSlices(analytics, by);
  return (
    <section data-testid="allocation-chart" className="rounded-md border border-line bg-layer-1 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="text-[13px] font-semibold">Allocation</h2>
        <span className="flex-1" />
        <div role="tablist" className="flex rounded-md bg-layer-2 p-0.5 text-[11.5px]">
          {(Object.keys(LABELS) as AllocationBy[]).map((k) => (
            <button
              key={k}
              type="button"
              role="tab"
              aria-selected={by === k}
              onClick={() => setBy(k)}
              className={clsx("rounded-sm px-2 py-0.5", by === k ? "bg-layer-1 text-fg" : "text-muted hover:text-fg")}
            >
              {LABELS[k]}
            </button>
          ))}
        </div>
      </div>
      {slices.length === 0 ? (
        <p className="text-[12.5px] text-subtle">{t("ยังไม่มีน้ำหนัก (ดึงราคาไม่ได้หรือยังไม่มี position)", "No weights yet (prices unavailable or no positions)")}</p>
      ) : (
        <div className="flex flex-wrap items-start gap-4">
          <Donut slices={slices} />
          <table className="min-w-40 flex-1 text-[12.5px]">
            <tbody>
              {slices.map((s) => (
                <tr key={s.name} data-testid="allocation-row" className="border-t border-line/50">
                  <td className="py-0.5 pr-2">
                    <span className="mr-1.5 inline-block size-2.5 rounded-sm align-middle" style={{ background: s.color }} />
                    {s.name}
                  </td>
                  <td className="py-0.5 text-right">
                    <ProvValue value={s.value} provenanceId={provenanceId} format="pct" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
