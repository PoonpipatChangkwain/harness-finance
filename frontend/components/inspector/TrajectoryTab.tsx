"use client";

/**
 * Trajectory (after dsh ui-trajectory `TrajectoryTable.tsx` / `TrajectoryTimeline.tsx`):
 * a timeline bar per tool and LLM call on the run's real clock (concurrent
 * calls overlap), plus a ledger of every stored event filterable by type.
 */

import { clsx } from "clsx";
import { useMemo, useState } from "react";

import { parseTs } from "@/lib/events";
import { formatDuration } from "@/lib/format";
import { useT } from "@/lib/i18n";
import type { RunView } from "@/stores/run-reducer";

interface Bar {
  key: string;
  label: string;
  start: number;
  end: number | null;
  kind: "tool" | "llm";
  status: string;
  depth: number;
}

export function timelineBars(view: RunView): Bar[] {
  const tools: Bar[] = Object.values(view.calls).map((c) => ({
    key: c.callId,
    label: c.tool,
    start: c.startedAt,
    end: c.finishedAt,
    kind: "tool",
    status: c.status,
    depth: c.parentCallId ? 1 : 0,
  }));
  const llm: Bar[] = view.llm.map((c) => ({
    key: `llm${c.seq}`,
    label: `llm ${c.purpose}${c.failed ? " ✗" : ""}`,
    start: c.startedAt,
    end: c.finishedAt,
    kind: "llm",
    status: c.failed ? "failed" : c.finishedAt ? "done" : "open",
    depth: 0,
  }));
  return [...llm, ...tools].sort((a, b) => a.start - b.start);
}

function Timeline({ view, now }: { view: RunView; now: number }) {
  const t = useT();
  const bars = timelineBars(view);
  const t0 = view.startedAt ?? bars[0]?.start ?? now;
  const t1 = view.finishedAt ?? now;
  const span = Math.max(1, t1 - t0);
  return (
    <div data-testid="trajectory-timeline" className="space-y-px p-2">
      {bars.map((b) => {
        const left = ((b.start - t0) / span) * 100;
        const width = Math.max(0.6, (((b.end ?? t1) - b.start) / span) * 100);
        return (
          <div key={b.key} className="flex items-center gap-2 text-[11px]">
            <span className="num w-28 shrink-0 truncate text-muted" style={{ paddingLeft: b.depth * 8 }} title={b.label}>
              {b.label}
            </span>
            <span className="relative h-2.5 flex-1 rounded-sm bg-layer-2">
              <span
                title={`${b.label} · ${formatDuration((b.end ?? t1) - b.start)}`}
                className={clsx(
                  "absolute inset-y-0 rounded-sm",
                  b.kind === "llm" ? (b.status === "failed" ? "bg-error/60" : "bg-accent/70") : b.status === "ok" ? "bg-done/80" : b.status === "running" ? "bg-ongoing fh-pulse" : "bg-warning/80",
                )}
                style={{ left: `${Math.min(99.4, left)}%`, width: `${Math.min(100 - left, width)}%` }}
              />
            </span>
          </div>
        );
      })}
      {bars.length === 0 ? <div className="text-subtle">{t("ยังไม่มี call", "No calls yet")}</div> : null}
    </div>
  );
}

export function TrajectoryTab({ view, now }: { view: RunView; now: number }) {
  const t = useT();
  const [type, setType] = useState("");
  const types = useMemo(() => [...new Set(view.events.map((e) => e.type))], [view.events]);
  const t0 = view.startedAt ?? (view.events[0] ? parseTs(view.events[0].ts) : 0);
  const rows = view.events.filter((e) => !type || e.type === type);
  return (
    <div data-testid="trajectory-tab" className="text-[12px]">
      <Timeline view={view} now={now} />
      <div className="sticky top-0 flex items-center gap-2 border-y border-line bg-layer-1 p-2">
        <select aria-label="Filter by event type" value={type} onChange={(e) => setType(e.target.value)} className="rounded-sm border border-line bg-layer-2 px-1 py-0.5">
          <option value="">{t("ทุก event", "All events")}</option>
          {types.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
        <span className="num text-subtle">{rows.length} events</span>
      </div>
      <table className="num w-full">
        <thead className="text-left text-subtle">
          <tr>
            <th className="px-2 py-1 font-normal">seq</th>
            <th className="px-2 py-1 font-normal">t+</th>
            <th className="px-2 py-1 font-normal">type</th>
            <th className="px-2 py-1 font-normal">node</th>
            <th className="px-2 py-1 font-normal">tool</th>
            <th className="px-2 py-1 text-right font-normal">dur</th>
          </tr>
        </thead>
        <tbody className="text-muted">
          {rows.map((e) => {
            const p = e.payload as Record<string, unknown>;
            const dur = typeof p.duration_ms === "number" ? p.duration_ms : null;
            const tool = typeof p.tool === "string" ? p.tool : "";
            return (
              <tr key={e.seq} className="border-t border-line/50">
                <td className="px-2 py-0.5 text-subtle">{e.seq}</td>
                <td className="px-2 py-0.5">{formatDuration(parseTs(e.ts) - t0)}</td>
                <td className="px-2 py-0.5 text-fg">{e.type}</td>
                <td className="max-w-24 truncate px-2 py-0.5">{e.node ?? ""}</td>
                <td className="max-w-28 truncate px-2 py-0.5">{tool}</td>
                <td className="px-2 py-0.5 text-right">{dur !== null ? formatDuration(dur) : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
