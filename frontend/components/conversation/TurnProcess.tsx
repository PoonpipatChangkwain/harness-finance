"use client";

/**
 * "Process · N steps · M tools · 12.3s" (ui-spec §4.1, after dsh
 * `TurnProcessNodeView.tsx`): open while the run is live, collapsed once it
 * finishes; a manual toggle wins and is remembered per run for the session.
 */

import { clsx } from "clsx";
import { ChevronRight } from "lucide-react";
import { useState } from "react";

import { StateDot, dotFor } from "@/components/ui/StateDot";
import { formatDuration } from "@/lib/format";
import { useT } from "@/lib/i18n";
import { isTerminal, stepCount, type RunView } from "@/stores/run-reducer";

import { CompactionItem, NodeHeader, NoticeRow, ReasoningRow } from "./ProcessRows";
import { ToolCallTree } from "./ToolCallTree";

const manualOpen = new Map<string, boolean>();

export function TurnProcess({ view, now }: { view: RunView; now: number }) {
  const t = useT();
  const [manual, setManual] = useState<boolean | undefined>(() => manualOpen.get(view.runId));
  const live = !isTerminal(view.status);
  const open = manual ?? live;
  const duration = view.usage.durationMs ?? (view.startedAt !== null ? now - view.startedAt : null);
  const lastIndex = view.items.length - 1;

  const toggle = () => {
    const next = !open;
    manualOpen.set(view.runId, next);
    setManual(next);
  };

  return (
    <div data-testid="turn-process" className="rounded-md border border-line bg-layer-1/50">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-[12.5px] text-muted hover:text-fg"
      >
        <ChevronRight className={clsx("size-3.5 transition-transform", open && "rotate-90")} />
        <StateDot state={dotFor(view.status)} label={view.status} />
        <span className="font-medium text-fg">Process</span>
        <span className="num">
          · {stepCount(view)} steps · {view.usage.toolCalls} tools · {formatDuration(duration)}
        </span>
        {!isTerminal(view.status) ? null : view.status !== "completed" ? (
          <span className="text-warning">· {view.status}</span>
        ) : null}
      </button>
      {open ? (
        <div className="space-y-0.5 border-t border-line px-1.5 py-1.5">
          {view.items.length === 0 ? <div className="fh-shimmer px-1 text-[12.5px]">{t("กำลังเริ่ม…", "Starting…")}</div> : null}
          {view.items.map((item, i) => {
            switch (item.kind) {
              case "node":
                return <NodeHeader key={`n${item.seq}`} item={item} />;
              case "reasoning":
                return <ReasoningRow key={`r${item.seq}`} text={item.text} pending={item.pending} live={live && i === lastIndex} />;
              case "batch":
                return <ToolCallTree key={`b${item.seq}`} callIds={item.callIds} calls={view.calls} runId={view.runId} />;
              case "compaction":
              case "microcompact":
                return <CompactionItem key={`c${item.seq}`} item={item} />;
              case "warning":
              case "guard":
                return <NoticeRow key={`w${item.seq}`} item={item} />;
            }
          })}
          {view.error ? <div className="px-1 text-[12.5px] text-error">{view.error}</div> : null}
        </div>
      ) : null}
    </div>
  );
}
