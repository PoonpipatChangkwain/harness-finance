"use client";

/**
 * Tool call tree + rows (ui-spec §4.1). Behaviour after deepseek-harness
 * ui-tool `ToolCallTree.tsx`, `ToolRow.tsx`, `ToolDetails.tsx`: one row per
 * call, children (subcalls) nested, concurrent calls of one batch joined by a
 * vertical line on the left, click to expand args / output / provenance / meta.
 */

import { clsx } from "clsx";
import {
  Brain,
  Calculator,
  ChartLine,
  ChevronRight,
  Globe,
  Puzzle,
  Search,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { useState } from "react";

import { JsonView } from "@/components/ui/JsonView";
import { StateDot, dotFor } from "@/components/ui/StateDot";
import { firstLine, formatDuration, shortId } from "@/lib/format";
import type { CallView } from "@/stores/run-reducer";
import { useUiStore } from "@/stores/ui-store";

const ICONS: Record<string, LucideIcon> = {
  data: ChartLine,
  compute: Calculator,
  search: Search,
  memory: Brain,
  skill: Puzzle,
  web: Globe,
};

export function categoryIcon(category: string): LucideIcon {
  return ICONS[category] ?? Wrench;
}

const PREVIEW_LIMIT = 2000;

export function ToolDetails({ call, runId }: { call: CallView; runId: string }) {
  const focusProvenance = useUiStore((s) => s.focusProvenance);
  const setTab = useUiStore((s) => s.setInspectorTab);
  const setOpen = useUiStore((s) => s.setInspectorOpen);
  const selectRun = useUiStore((s) => s.selectRun);
  const preview = call.preview ?? "";
  return (
    <div className="ml-6 mt-1 mb-2 space-y-2 rounded-md border border-line bg-layer-1 p-2 text-xs">
      <section>
        <h4 className="mb-1 text-[11px] font-medium uppercase tracking-wide text-subtle">Arguments</h4>
        <JsonView data={call.args} />
      </section>
      {call.error ? (
        <section>
          <h4 className="mb-1 text-[11px] font-medium uppercase tracking-wide text-error">Error</h4>
          <pre className="num whitespace-pre-wrap break-words text-error">
            {call.error.type}: {call.error.message}
          </pre>
        </section>
      ) : null}
      {preview ? (
        <section>
          <h4 className="mb-1 flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-subtle">
            Output
            {call.truncated || preview.length > PREVIEW_LIMIT ? <span className="normal-case text-warning">ตัดให้สั้น</span> : null}
          </h4>
          <pre className="num max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-sm bg-layer-2 p-2 text-muted">
            {preview.slice(0, PREVIEW_LIMIT)}
          </pre>
          <button
            type="button"
            className="mt-1 text-accent hover:underline"
            onClick={() => {
              selectRun(runId);
              setTab("scratchpad");
              setOpen(true);
            }}
          >
            เปิดเต็มใน Scratchpad
          </button>
        </section>
      ) : null}
      {call.provenanceIds.length ? (
        <section>
          <h4 className="mb-1 text-[11px] font-medium uppercase tracking-wide text-subtle">
            Provenance · {call.provenanceIds.length}
          </h4>
          <div className="flex flex-wrap gap-1">
            {call.provenanceIds.map((pid) => (
              <button
                key={pid}
                type="button"
                title={pid}
                onClick={() => focusProvenance(runId, pid)}
                className="num rounded-sm border border-line px-1.5 py-px text-[11px] text-muted hover:border-accent hover:text-accent"
              >
                {shortId(pid)}
              </button>
            ))}
          </div>
        </section>
      ) : null}
      <section className="num flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-subtle">
        <span>call {shortId(call.callId)}</span>
        {call.batchId ? <span>batch {shortId(call.batchId)}</span> : null}
        <span>{call.readOnly ? "read-only" : "writes"}</span>
        <span>status {call.status}</span>
        {call.node ? <span>node {call.node}</span> : null}
      </section>
    </div>
  );
}

export function ToolRow({
  call,
  runId,
  depth = 0,
  defaultOpen = false,
}: {
  call: CallView;
  runId: string;
  depth?: number;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const Icon = categoryIcon(call.category);
  const running = call.status === "running";
  const failed = dotFor(call.status) === "error";
  const text = failed && call.error ? firstLine(call.error.message) : (call.summary ?? "");
  return (
    <div data-testid="tool-row" data-status={call.status}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="group flex w-full min-w-0 items-center gap-2 rounded-sm px-1 py-0.5 text-left hover:bg-layer-2"
        style={{ paddingLeft: depth * 16 + 4 }}
      >
        <ChevronRight className={clsx("size-3 shrink-0 text-subtle transition-transform", open && "rotate-90")} />
        <Icon className="size-3.5 shrink-0 text-muted" aria-hidden />
        <span className="num shrink-0 text-[13px] text-fg">{call.tool}</span>
        <span
          className={clsx(
            "min-w-0 flex-1 truncate text-[12.5px]",
            failed ? "text-error" : "text-muted",
            running && call.progress && "fh-shimmer",
          )}
        >
          {running ? (call.progress ?? summarizeArgs(call.args)) : text}
        </span>
        <StateDot state={dotFor(call.status)} label={call.status} />
        <span className="num w-12 shrink-0 text-right text-[11px] text-subtle">
          {call.durationMs !== null ? formatDuration(call.durationMs) : running ? "…" : ""}
        </span>
      </button>
      {open ? <ToolDetails call={call} runId={runId} /> : null}
    </div>
  );
}

function summarizeArgs(args: Record<string, unknown>): string {
  return Object.values(args)
    .filter((v) => typeof v === "string" || typeof v === "number")
    .slice(0, 3)
    .join(" · ");
}

function CallNode({
  callId,
  calls,
  runId,
  depth,
}: {
  callId: string;
  calls: Record<string, CallView>;
  runId: string;
  depth: number;
}) {
  const call = calls[callId];
  if (!call) return null;
  return (
    <>
      <ToolRow call={call} runId={runId} depth={depth} />
      {call.children.map((child) => (
        <CallNode key={child} callId={child} calls={calls} runId={runId} depth={depth + 1} />
      ))}
    </>
  );
}

/** One batch of root calls; a batch of 2+ ran concurrently and gets the joining line. */
export function ToolCallTree({
  callIds,
  calls,
  runId,
}: {
  callIds: string[];
  calls: Record<string, CallView>;
  runId: string;
}) {
  const concurrent = callIds.length > 1;
  return (
    <div
      data-testid="tool-batch"
      data-concurrent={concurrent}
      className={clsx("relative", concurrent && "border-l-2 border-accent-muted pl-1")}
      title={concurrent ? `${callIds.length} calls ran concurrently` : undefined}
    >
      {callIds.map((id) => (
        <CallNode key={id} callId={id} calls={calls} runId={runId} depth={0} />
      ))}
    </div>
  );
}
