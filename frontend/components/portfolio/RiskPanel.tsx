"use client";

/**
 * Risk panel (ui-spec §6): volatility, max drawdown, beta, VaR/CVaR, HHI + effective N,
 * a diverging correlation heatmap and the contribution-to-risk table — all computed in the
 * backend, each with provenance.
 */

import type { ReactNode } from "react";

import { formatPct } from "@/lib/portfolio-format";
import type { PortfolioAnalytics } from "@/lib/portfolio-api";

import { ProvValue } from "./ProvValue";

function Metric({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <div className="rounded-md bg-layer-2/60 px-2.5 py-1.5" title={hint}>
      <div className="text-[11px] text-muted">{label}</div>
      <div className="text-[14px] font-semibold">{children}</div>
    </div>
  );
}

/** −1 → red, 0 → neutral, +1 → blue (diverging). */
export function correlationColor(v: number): string {
  const x = Math.max(-1, Math.min(1, v));
  const alpha = Math.abs(x) * 0.85 + 0.05;
  return x >= 0 ? `rgba(78, 161, 255, ${alpha.toFixed(3)})` : `rgba(240, 106, 106, ${alpha.toFixed(3)})`;
}

export function CorrelationHeatmap({
  matrix,
  provenanceId,
  labels,
}: {
  matrix: Record<string, Record<string, number>>;
  provenanceId: string;
  labels: Map<string, string>;
}) {
  const keys = Object.keys(matrix);
  if (keys.length < 2) return <p className="text-[12px] text-subtle">ต้องมีอย่างน้อย 2 ตัวจึงแสดง correlation</p>;
  const name = (k: string) => labels.get(k) ?? k;
  return (
    <div className="overflow-x-auto">
      <table data-testid="correlation-heatmap" className="num border-separate border-spacing-0.5 text-[10.5px]">
        <thead>
          <tr>
            <th />
            {keys.map((k) => (
              <th key={k} className="max-w-14 truncate px-0.5 font-normal text-subtle" title={k}>
                {name(k)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {keys.map((row) => (
            <tr key={row}>
              <th className="whitespace-nowrap pr-1 text-right font-normal text-subtle">{name(row)}</th>
              {keys.map((c) => {
                const v = matrix[row]?.[c] ?? 0;
                return (
                  <td
                    key={c}
                    data-testid="corr-cell"
                    title={`${name(row)} × ${name(c)}: ${v.toFixed(2)}`}
                    style={{ background: correlationColor(v) }}
                    className="size-9 min-w-9 rounded-sm text-center text-fg"
                  >
                    <ProvValue value={v} provenanceId={provenanceId} format="ratio" digits={2} className="text-[10.5px] no-underline" />
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-1 flex items-center gap-1 text-[10.5px] text-subtle">
        <span className="inline-block h-2 w-8 rounded-sm" style={{ background: "linear-gradient(90deg, rgba(240,106,106,.9), rgba(78,161,255,.05), rgba(78,161,255,.9))" }} />
        −1 · 0 · +1
      </div>
    </div>
  );
}

export function RiskPanel({ analytics }: { analytics: PortfolioAnalytics }) {
  const r = analytics.risk;
  const c = analytics.concentration;
  const labels = new Map(analytics.holdings.map((h) => [h.symbol, h.display]));
  const contribution = analytics.holdings
    .filter((h) => h.risk_contribution)
    .sort((a, b) => (b.risk_contribution?.value ?? 0) - (a.risk_contribution?.value ?? 0));
  return (
    <section data-testid="risk-panel" className="rounded-md border border-line bg-layer-1 p-3">
      <div className="mb-2 flex flex-wrap items-baseline gap-2">
        <h2 className="text-[13px] font-semibold">Risk</h2>
        {r ? (
          <span className="text-[11.5px] text-subtle">
            {r.observations} วัน ({r.start} → {r.end}) · {r.method}
          </span>
        ) : null}
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Metric label="Volatility (ต่อปี)">{r ? <ProvValue pv={r.volatility} format="pct" /> : "–"}</Metric>
        <Metric label="Max drawdown">{r ? <ProvValue pv={r.max_drawdown} format="pct" /> : "–"}</Metric>
        <Metric label={`Beta vs ${r?.benchmark ?? "benchmark"}`}>{r?.beta ? <ProvValue pv={r.beta} format="ratio" digits={2} /> : "–"}</Metric>
        <Metric label="VaR 95% / CVaR 95% (1 วัน)">
          {r ? (
            <>
              <ProvValue pv={r.var_95} format="pct" /> / <ProvValue pv={r.cvar_95} format="pct" />
            </>
          ) : (
            "–"
          )}
        </Metric>
        <Metric label="HHI" hint="Herfindahl–Hirschman: Σ w² (1/N = กระจายเท่ากัน)">
          {c ? <ProvValue value={c.hhi} provenanceId={c.provenance_id} format="ratio" /> : "–"}
        </Metric>
        <Metric label="Effective N">{c ? <ProvValue value={c.effective_n} provenanceId={c.provenance_id} format="number" digits={1} /> : "–"}</Metric>
        <Metric label="Top 1 / Top 3">
          {c ? (
            <>
              <ProvValue value={c.top1} provenanceId={c.provenance_id} format="pct" digits={1} /> /{" "}
              <ProvValue value={c.top3} provenanceId={c.provenance_id} format="pct" digits={1} />
            </>
          ) : (
            "–"
          )}
        </Metric>
      </div>
      {!r ? <p className="mt-2 text-[12px] text-subtle">ยังคำนวณความเสี่ยงไม่ได้ (ดูคำเตือนด้านบน)</p> : null}
      {r ? (
        <div className="mt-3 grid gap-4 lg:grid-cols-[auto_1fr]">
          <div>
            <h3 className="mb-1 text-[12px] text-muted">Correlation</h3>
            <CorrelationHeatmap matrix={r.correlation} provenanceId={r.correlation_provenance_id} labels={labels} />
          </div>
          <div>
            <h3 className="mb-1 text-[12px] text-muted">Contribution to risk</h3>
            <table data-testid="risk-contribution" className="w-full text-[12.5px]">
              <thead className="text-left text-[11px] text-subtle">
                <tr>
                  <th className="font-normal">Symbol</th>
                  <th className="text-right font-normal">Weight</th>
                  <th className="text-right font-normal">Risk share</th>
                  <th className="w-1/3" />
                </tr>
              </thead>
              <tbody>
                {contribution.map((h) => (
                  <tr key={h.symbol} className="border-t border-line/50">
                    <td className="py-0.5">{h.display}</td>
                    <td className="py-0.5 text-right">
                      <ProvValue pv={h.weight} format="pct" digits={1} />
                    </td>
                    <td className="py-0.5 text-right">
                      <ProvValue pv={h.risk_contribution} format="pct" digits={1} />
                    </td>
                    <td className="pl-2">
                      <div className="h-1.5 rounded-sm bg-layer-2">
                        <div
                          className="h-full rounded-sm bg-warning"
                          style={{ width: `${Math.min(100, Math.max(0, (h.risk_contribution?.value ?? 0) * 100))}%` }}
                          title={formatPct(h.risk_contribution?.value)}
                        />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </section>
  );
}
