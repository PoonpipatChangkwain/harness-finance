"use client";

/** Per-turn usage pills (after dsh `StatsPills.tsx` / `TurnUsagePanel.tsx`); hover for the per-purpose breakdown. */

import { useState } from "react";

import { formatCost, formatDuration, formatTokens } from "@/lib/format";
import type { UsageView } from "@/stores/run-reducer";

function Pill({ children, title }: { children: React.ReactNode; title?: string }) {
  return (
    <span title={title} className="num rounded-full border border-line px-2 py-px text-[11px] text-muted">
      {children}
    </span>
  );
}

export function StatsPills({ usage }: { usage: UsageView }) {
  const [open, setOpen] = useState(false);
  const purposes = Object.entries(usage.byPurpose);
  return (
    <div
      data-testid="stats-pills"
      className="relative flex flex-wrap items-center gap-1"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <Pill title="input tokens">in {formatTokens(usage.in)}</Pill>
      <Pill title="output tokens">out {formatTokens(usage.out)}</Pill>
      <Pill title="cost (USD)">{formatCost(usage.cost)}</Pill>
      <Pill title="duration">{formatDuration(usage.durationMs)}</Pill>
      <Pill title="LLM calls">{usage.llmCalls} LLM calls</Pill>
      {open && purposes.length ? (
        <div className="absolute left-0 top-full z-20 mt-1 rounded-md border border-line-strong bg-layer-1 p-2 text-xs shadow-lg">
          <table className="num">
            <thead className="text-subtle">
              <tr>
                <th className="pr-3 text-left font-normal">purpose</th>
                <th className="pr-3 text-right font-normal">calls</th>
                <th className="pr-3 text-right font-normal">in</th>
                <th className="pr-3 text-right font-normal">out</th>
                <th className="text-right font-normal">cost</th>
              </tr>
            </thead>
            <tbody className="text-muted">
              {purposes.map(([purpose, u]) => (
                <tr key={purpose}>
                  <td className="pr-3">{purpose}</td>
                  <td className="pr-3 text-right">{u.calls}</td>
                  <td className="pr-3 text-right">{formatTokens(u.in)}</td>
                  <td className="pr-3 text-right">{formatTokens(u.out)}</td>
                  <td className="text-right">{formatCost(u.cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
