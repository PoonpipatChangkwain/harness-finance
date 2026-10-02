"use client";

/**
 * Debate tab (ui-spec §5): Bull | Bear or Aggressive | Conservative | Neutral columns,
 * messages by round, and the judge's call (research manager / portfolio manager) below.
 */

import { clsx } from "clsx";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { VerdictBadge } from "@/components/trade/DecisionCard";
import type { DebateKind } from "@/lib/trade";
import type { DebateTurn, TradeRunDetail } from "@/lib/trade-api";

const SIDES: Record<DebateKind, { id: string; label: string; tone: string }[]> = {
  invest: [
    { id: "bull", label: "Bull", tone: "text-up" },
    { id: "bear", label: "Bear", tone: "text-down" },
  ],
  risk: [
    { id: "aggressive", label: "Aggressive", tone: "text-down" },
    { id: "conservative", label: "Conservative", tone: "text-accent" },
    { id: "neutral", label: "Neutral", tone: "text-muted" },
  ],
};

/** Turns grouped by round: round n = the n-th turn of each speaker. */
export function debateRounds(history: DebateTurn[], speakers: string[]): Record<string, string>[] {
  const rounds: Record<string, string>[] = [];
  const seen: Record<string, number> = {};
  for (const turn of history) {
    if (!speakers.includes(turn.speaker)) continue;
    const i = seen[turn.speaker] ?? 0;
    seen[turn.speaker] = i + 1;
    rounds[i] = { ...(rounds[i] ?? {}), [turn.speaker]: turn.text };
  }
  return rounds;
}

export function DebateView({ detail, kind }: { detail: TradeRunDetail | undefined; kind: DebateKind }) {
  const sides = SIDES[kind];
  const history = (kind === "invest" ? detail?.invest_debate?.history : detail?.risk_debate?.history) ?? [];
  const rounds = debateRounds(history, sides.map((s) => s.id));
  const judge =
    kind === "invest"
      ? detail?.research_plan
        ? {
            title: "Research manager",
            verdict: String(detail.research_plan.verdict ?? ""),
            text: `**${String(detail.research_plan.stance ?? "")}** — ${String(detail.research_plan.thesis ?? "")}`,
          }
        : null
      : detail?.decision
        ? {
            title: "Portfolio manager",
            verdict: detail.decision.verdict,
            text: `**${detail.decision.rating}** — ${detail.decision.summary || detail.decision.decision.reason}`,
          }
        : null;

  return (
    <div data-testid="debate-view" className="space-y-3 p-3 text-[12.5px]">
      {!rounds.length ? <p className="text-subtle">ยังไม่มีการ debate</p> : null}
      {rounds.map((round, i) => (
        <section key={i} data-testid="debate-round-row">
          <h4 className="mb-1 text-[11px] uppercase tracking-wide text-subtle">รอบ {i + 1}</h4>
          <div className={clsx("grid gap-2", sides.length === 3 ? "grid-cols-3" : "grid-cols-2")}>
            {sides.map((s) => (
              <div key={s.id} className="min-w-0 rounded-md border border-line bg-layer-1 p-2">
                <div className={clsx("mb-1 text-[11.5px] font-semibold", s.tone)}>{s.label}</div>
                {round[s.id] ? (
                  <div className="fh-markdown break-words text-muted">
                    <Markdown remarkPlugins={[remarkGfm]}>{round[s.id]}</Markdown>
                  </div>
                ) : (
                  <span className="text-subtle">…</span>
                )}
              </div>
            ))}
          </div>
        </section>
      ))}
      {judge ? (
        <section data-testid="debate-judge" className="rounded-md border border-line-strong bg-layer-2 p-2">
          <div className="mb-1 flex items-center gap-2">
            <span className="text-[11.5px] font-semibold">{judge.title}</span>
            {judge.verdict ? <VerdictBadge verdict={judge.verdict} /> : null}
          </div>
          <div className="fh-markdown text-muted">
            <Markdown remarkPlugins={[remarkGfm]}>{judge.text}</Markdown>
          </div>
        </section>
      ) : null}
    </div>
  );
}
