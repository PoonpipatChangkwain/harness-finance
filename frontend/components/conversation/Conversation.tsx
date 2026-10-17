"use client";

/**
 * Conversation = header + transcript + composer (ui-spec §4.1). The header
 * shows the session title, model chips, ContextMeter, cost and duration of
 * the latest run; the transcript follows new output while the user is at the
 * bottom. Long transcripts are not virtualized yet (fine up to ~50 turns).
 */

import { ArrowDown } from "lucide-react";
import { useRef, type ReactNode } from "react";

import { StateDot, dotFor } from "@/components/ui/StateDot";
import { formatCost, formatDuration } from "@/lib/format";
import { useScrollFollow } from "@/lib/use-scroll-follow";
import type { RunView } from "@/stores/run-reducer";

import { ContextMeter } from "./ContextMeter";
import { Turn, type TurnData } from "./Turn";

export function ConversationHeader({
  title,
  view,
  now,
  resumable,
  onResume,
}: {
  title: string;
  view: RunView | undefined;
  now: number;
  /** (P09) run left ``waiting_approval``/``interrupted`` by a crash or a prior server run. */
  resumable?: boolean;
  onResume?: () => void;
}) {
  const duration = view ? (view.usage.durationMs ?? (view.startedAt !== null ? now - view.startedAt : null)) : null;
  return (
    <header className="flex min-h-11 flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-2 pr-12">
      <h1 data-testid="session-title" className="min-w-0 truncate text-[14px] font-semibold">
        {title}
      </h1>
      {view ? (
        <div className="num flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-muted">
          <StateDot state={dotFor(view.status)} label={view.status} />
          {view.models.deep ? <span title="deep tier" className="rounded-sm bg-layer-2 px-1.5">{view.models.deep}</span> : null}
          {view.models.quick && view.models.quick !== view.models.deep ? (
            <span title="quick tier" className="rounded-sm bg-layer-2 px-1.5">{view.models.quick}</span>
          ) : null}
          <ContextMeter used={view.context.used} window={view.context.window} />
          <span title="cost of the latest run">{formatCost(view.usage.cost)}</span>
          <span title="duration of the latest run">⏱ {formatDuration(duration)}</span>
        </div>
      ) : null}
      {resumable && onResume ? (
        <button
          type="button"
          data-testid="resume-run"
          onClick={onResume}
          className="ml-auto inline-flex items-center rounded-md bg-accent/20 px-2 py-0.5 text-[12px] font-medium text-accent"
        >
          Resume
        </button>
      ) : null}
    </header>
  );
}

export function Conversation({
  header,
  turns,
  now,
  composer,
  empty,
  skillLabel,
}: {
  header: ReactNode;
  turns: TurnData[];
  now: number;
  composer: ReactNode;
  empty?: ReactNode;
  skillLabel?: (s: string) => string;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const { atBottom, scrollToBottom } = useScrollFollow(scroller);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {header}
      <div ref={scroller} data-testid="transcript" className="relative min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-4">
          {turns.length === 0 ? empty : turns.map((t, i) => <Turn key={t.key} turn={t} now={now} skillLabel={skillLabel} latest={i === turns.length - 1} />)}
        </div>
      </div>
      <div className="relative mx-auto w-full max-w-3xl px-4 pb-4">
        {!atBottom ? (
          <button
            type="button"
            onClick={() => scrollToBottom(true)}
            className="absolute -top-10 left-1/2 inline-flex -translate-x-1/2 items-center gap-1 rounded-full border border-line-strong bg-layer-1 px-3 py-1 text-[12px] text-muted shadow hover:text-fg"
          >
            <ArrowDown className="size-3.5" aria-hidden /> ล่าสุด
          </button>
        ) : null}
        {composer}
      </div>
    </div>
  );
}
