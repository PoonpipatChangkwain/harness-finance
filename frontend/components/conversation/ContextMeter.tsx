"use client";

/**
 * Context occupancy (ui-spec §4.1, after dsh ui-conversation `context-occupancy.ts`):
 * percent = min(100, round(used / window × 100)); < 60 normal, 60–85 amber, > 85 red.
 */

import { clsx } from "clsx";

import { formatTokens } from "@/lib/format";

/** Mirrors backend ContextConfig: full compaction at window − 20k summary reserve − 13k buffer. */
export const COMPACT_RESERVE_TOKENS = 20_000 + 13_000;

export function contextPercent(used: number, window: number): number {
  if (!window) return 0;
  return Math.min(100, Math.round((used / window) * 100));
}

export function contextLevel(percent: number): "normal" | "warning" | "danger" {
  if (percent > 85) return "danger";
  if (percent >= 60) return "warning";
  return "normal";
}

const BAR: Record<ReturnType<typeof contextLevel>, string> = {
  normal: "bg-accent",
  warning: "bg-warning",
  danger: "bg-error",
};

export function ContextMeter({ used, window }: { used: number; window: number }) {
  const percent = contextPercent(used, window);
  const level = contextLevel(percent);
  const threshold = Math.max(0, window - COMPACT_RESERVE_TOKENS);
  const title = window
    ? `${formatTokens(used)} / ${formatTokens(window)} tokens · full compaction at ~${formatTokens(threshold)} · microcompact after 8 tool results or 80k tokens`
    : "no context usage yet";
  return (
    <span data-testid="context-meter" data-level={level} title={title} className="inline-flex items-center gap-1.5">
      <span className="relative h-1.5 w-16 overflow-hidden rounded-full bg-layer-2">
        <span className={clsx("absolute inset-y-0 left-0 rounded-full", BAR[level])} style={{ width: `${percent}%` }} />
      </span>
      <span className={clsx("num text-[11px]", level === "danger" ? "text-error" : level === "warning" ? "text-warning" : "text-muted")}>
        {percent}%
      </span>
    </span>
  );
}
