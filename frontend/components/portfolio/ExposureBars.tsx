"use client";

/** Sector exposure as horizontal bars against the policy's sector limit (red line). */

import { clsx } from "clsx";

import { formatPct } from "@/lib/portfolio-format";
import type { PortfolioAnalytics } from "@/lib/portfolio-api";

import { ProvValue } from "./ProvValue";
import { useT } from "@/lib/i18n";

export function ExposureBars({
  analytics,
  limit,
  limitLabel,
}: {
  analytics: PortfolioAnalytics;
  /** max sector weight (0–1) from Settings › Policy, or the advisor's balanced default */
  limit: number;
  limitLabel: string;
}) {
  const t = useT();
  const sectors = Object.entries(analytics.exposure.sector ?? {})
    .filter(([k, v]) => k !== "CASH" && v > 0)
    .sort((a, b) => b[1] - a[1]);
  const pid = analytics.exposure_provenance.sector ?? null;
  const scale = Math.max(limit, ...sectors.map(([, v]) => v), 0.01);
  return (
    <section data-testid="exposure-bars" className="rounded-md border border-line bg-layer-1 p-3">
      <div className="mb-2 flex items-center gap-2">
        <h2 className="text-[13px] font-semibold">Sector exposure</h2>
        <span className="text-[11.5px] text-subtle">
          {t("เส้นแดง", "Red line")} = limit {formatPct(limit, 0)} ({limitLabel})
        </span>
      </div>
      {sectors.length === 0 ? (
        <p className="text-[12.5px] text-subtle">{t("ยังไม่มีข้อมูล sector", "No sector data yet")}</p>
      ) : (
        <ul className="space-y-1.5">
          {sectors.map(([name, w]) => {
            const over = w > limit + 1e-9 && name !== "Unknown";
            return (
              <li key={name} data-testid="exposure-row" data-over={over} className="text-[12px]">
                <div className="flex justify-between">
                  <span className={clsx(over && "text-error")}>{name}</span>
                  <ProvValue value={w} provenanceId={pid} format="pct" className={over ? "text-error" : undefined} />
                </div>
                <div className="relative mt-0.5 h-2 rounded-sm bg-layer-2">
                  <div
                    className={clsx("h-full rounded-sm", over ? "bg-error" : "bg-accent")}
                    style={{ width: `${(w / scale) * 100}%` }}
                  />
                  <div className="absolute inset-y-[-3px] w-0.5 bg-error" style={{ left: `${(limit / scale) * 100}%` }} aria-hidden />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
