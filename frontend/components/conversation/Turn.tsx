"use client";

/** One turn: user bubble (+ scenario chip) → TurnProcess → Answer → StatsPills (ui-spec §4.1). */

import { clsx } from "clsx";
import { Bot, Briefcase, Puzzle } from "lucide-react";

import { useRun } from "@/stores/run-store";
import { useUiStore } from "@/stores/ui-store";

import { Answer } from "./Answer";
import { StatsPills } from "./StatsPills";
import { TurnProcess } from "./TurnProcess";

export interface TurnData {
  key: string;
  text: string;
  skill: string | null;
  runId: string | null;
  /** "provider:model" picked for this message, if not the default */
  llm?: string | null;
  /** (P13) names of the portfolios whose data this turn used */
  portfolios?: string[] | null;
}

export function Turn({ turn, now, skillLabel }: { turn: TurnData; now: number; skillLabel?: (s: string) => string }) {
  const view = useRun(turn.runId);
  const selected = useUiStore((s) => s.selectedRunId === turn.runId && turn.runId !== null);
  const selectRun = useUiStore((s) => s.selectRun);
  const draft = view ? view.answerDraft.text + view.answerDraft.pending : "";

  return (
    <article
      data-testid="turn"
      onClickCapture={() => turn.runId && selectRun(turn.runId)}
      className={clsx("space-y-3 border-l-2 py-1 pl-3 transition-colors", selected ? "border-accent/60" : "border-transparent")}
    >
      <div className="flex justify-end">
        <div className="max-w-[85%] space-y-1 rounded-lg bg-accent-muted px-3 py-2">
          {turn.llm ? (
            <span className="num mr-1 inline-flex items-center gap-1 rounded-full border border-line-strong px-2 py-px text-[11px] text-muted">
              <Bot className="size-3" aria-hidden />
              {turn.llm}
            </span>
          ) : null}
          {turn.portfolios?.length ? (
            <span
              data-testid="turn-portfolios"
              className="mr-1 inline-flex items-center gap-1 rounded-full border border-done/40 px-2 py-px text-[11px] text-done"
            >
              <Briefcase className="size-3" aria-hidden />
              ใช้พอร์ต: {turn.portfolios.join(", ")}
            </span>
          ) : null}
          {turn.skill ? (
            <span className="inline-flex items-center gap-1 rounded-full border border-accent/40 px-2 py-px text-[11px] text-accent">
              <Puzzle className="size-3" aria-hidden />
              {skillLabel ? skillLabel(turn.skill) : turn.skill}
            </span>
          ) : null}
          <p className="whitespace-pre-wrap text-[14px] text-fg">{turn.text}</p>
        </div>
      </div>
      {view ? <TurnProcess view={view} now={now} /> : turn.runId ? <div className="fh-shimmer text-[12.5px]">กำลังโหลด run…</div> : null}
      {view?.answer ? (
        <Answer
          markdown={view.answer.markdown}
          citations={view.answer.citations}
          unverified={view.answer.unverified}
          runId={view.runId}
        />
      ) : draft ? (
        <Answer markdown={draft} citations={[]} unverified={[]} runId={view?.runId ?? ""} streaming />
      ) : null}
      {view && (view.answer || view.usage.llmCalls > 0) ? <StatsPills usage={view.usage} /> : null}
    </article>
  );
}
