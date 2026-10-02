"use client";

/**
 * Small rows inside TurnProcess: reasoning (after dsh `ReasoningRow.tsx`),
 * compaction markers (after dsh `CompactionItem.tsx`), warning / guard rows
 * and workflow node headers.
 */

import { clsx } from "clsx";
import { Ban, ChevronRight, Layers, Scissors, Sparkles, TriangleAlert } from "lucide-react";
import { useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { StateDot, dotFor } from "@/components/ui/StateDot";
import { formatDuration, formatTokens } from "@/lib/format";
import type { ProcessItem } from "@/stores/run-reducer";

export function ReasoningRow({ text, pending, live }: { text: string; pending: string; live?: boolean }) {
  const [open, setOpen] = useState(false);
  const full = text + pending;
  if (!full.trim()) return null;
  return (
    <button
      type="button"
      onClick={() => setOpen((v) => !v)}
      aria-expanded={open}
      className="flex w-full items-start gap-2 rounded-sm px-1 py-0.5 text-left hover:bg-layer-2"
    >
      <Sparkles className={clsx("mt-0.5 size-3.5 shrink-0 text-subtle", live && "fh-pulse")} aria-hidden />
      <span
        data-testid="reasoning"
        className={clsx("min-w-0 flex-1 whitespace-pre-wrap text-[12.5px] italic text-muted", !open && "line-clamp-2")}
      >
        {full}
      </span>
    </button>
  );
}

type CompactionItemProps = Extract<ProcessItem, { kind: "compaction" }> | Extract<ProcessItem, { kind: "microcompact" }>;

export function CompactionItem({ item }: { item: CompactionItemProps }) {
  const [open, setOpen] = useState(false);
  if (item.kind === "microcompact") {
    return (
      <div data-testid="compaction" data-kind="micro" className="flex items-center gap-2 px-1 py-0.5 text-[12px] text-subtle">
        <Scissors className="size-3.5 shrink-0" aria-hidden />
        Cleared {item.cleared} old tool results (−{formatTokens(item.tokensSaved)} tokens)
      </div>
    );
  }
  if (item.phase === "start") {
    return (
      <div data-testid="compaction" data-kind="start" className="flex items-center gap-2 px-1 py-0.5 text-[12.5px]">
        <Layers className="size-3.5 shrink-0 text-accent" aria-hidden />
        <span className="fh-shimmer">Compacting context · {formatTokens(item.preTokens)} tokens…</span>
      </div>
    );
  }
  if (item.phase === "fallback_clear") {
    return (
      <div data-testid="compaction" data-kind="fallback" className="flex items-center gap-2 px-1 py-0.5 text-[12.5px] text-warning">
        <Scissors className="size-3.5 shrink-0" aria-hidden />
        Context trimmed · removed {item.cleared ?? 0} messages · kept {item.kept ?? 0} tool results
      </div>
    );
  }
  if (item.phase === "failed") {
    return (
      <div data-testid="compaction" data-kind="failed" className="flex items-center gap-2 px-1 py-0.5 text-[12.5px] text-error">
        <Layers className="size-3.5 shrink-0" aria-hidden />
        Compaction failed{item.summary ? ` · ${item.summary}` : ""}
      </div>
    );
  }
  return (
    <div data-testid="compaction" data-kind="full">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-sm px-1 py-0.5 text-left text-[12.5px] hover:bg-layer-2"
      >
        <ChevronRight className={clsx("size-3 shrink-0 text-subtle transition-transform", open && "rotate-90")} />
        <Layers className="size-3.5 shrink-0 text-accent" aria-hidden />
        <span>
          Context compacted ·{" "}
          <span className="num">
            {formatTokens(item.preTokens)} → {item.postTokens !== null ? formatTokens(item.postTokens) : "?"}
          </span>{" "}
          tokens
        </span>
      </button>
      {open && item.summary ? (
        <div className="fh-markdown ml-6 mt-1 rounded-md border border-line bg-layer-1 p-2 text-xs text-muted">
          <Markdown remarkPlugins={[remarkGfm]}>{item.summary}</Markdown>
        </div>
      ) : null}
    </div>
  );
}

export function NoticeRow({ item }: { item: Extract<ProcessItem, { kind: "warning" } | { kind: "guard" }> }) {
  const [open, setOpen] = useState(false);
  const guard = item.kind === "guard";
  const title = guard ? `blocked: ${item.guardKind.replace("_", " ")}` : item.code;
  const detail = guard ? item.detail : item.message;
  const Icon = guard ? Ban : TriangleAlert;
  return (
    <button
      type="button"
      onClick={() => setOpen((v) => !v)}
      aria-expanded={open}
      data-testid={guard ? "guard-row" : "warning-row"}
      className="flex w-full items-start gap-2 rounded-sm px-1 py-0.5 text-left text-[12.5px] hover:bg-layer-2"
    >
      <Icon className={clsx("mt-0.5 size-3.5 shrink-0", guard ? "text-error" : "text-warning")} aria-hidden />
      <span className={clsx("shrink-0 font-medium", guard ? "text-error" : "text-warning")}>{title}</span>
      <span className={clsx("min-w-0 flex-1 text-muted", open ? "whitespace-pre-wrap break-words" : "truncate")}>
        {detail}
      </span>
    </button>
  );
}

export function NodeHeader({ item }: { item: Extract<ProcessItem, { kind: "node" }> }) {
  return (
    <div className="mt-1 flex items-center gap-2 px-1 pt-1 text-[11px] uppercase tracking-wide text-subtle first:mt-0">
      <StateDot state={dotFor(item.status)} label={item.status} />
      <span className="font-medium">{item.label}</span>
      {item.durationMs !== null ? <span className="num normal-case">{formatDuration(item.durationMs)}</span> : null}
      {item.summary ? <span className="truncate normal-case tracking-normal">· {item.summary}</span> : null}
    </div>
  );
}
