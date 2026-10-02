"use client";

/** Context window of a run: gauge, compaction thresholds, tool-result counts, compaction history. */

import { COMPACT_RESERVE_TOKENS, ContextMeter, contextPercent } from "@/components/conversation/ContextMeter";
import { formatTime, formatTokens } from "@/lib/format";
import type { RunView } from "@/stores/run-reducer";

export function ContextTab({ view }: { view: RunView }) {
  const { used, window, compactions } = view.context;
  const results = Object.values(view.calls).filter((c) => c.finishedAt !== null).length;
  const cleared = compactions.filter((c) => c.kind === "micro").reduce((n, c) => n + (c.cleared ?? 0), 0);
  const rows: [string, string][] = [
    ["used / window", `${formatTokens(used)} / ${formatTokens(window)} (${contextPercent(used, window)}%)`],
    ["full compaction at", window ? `~${formatTokens(window - COMPACT_RESERVE_TOKENS)} tokens` : "–"],
    ["microcompact", "> 8 tool results (keep 4) or > 80k tokens"],
    ["tool results", `${results} · cleared by microcompact ${cleared} · active ${Math.max(0, results - cleared)}`],
  ];
  return (
    <div data-testid="context-tab" className="space-y-3 p-3 text-[12.5px]">
      <div className="flex items-center gap-2">
        <ContextMeter used={used} window={window} />
        <span className="text-muted">context occupancy</span>
      </div>
      <dl className="num grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-subtle">{k}</dt>
            <dd className="text-fg">{v}</dd>
          </div>
        ))}
      </dl>
      <section>
        <h3 className="mb-1 text-[11px] uppercase tracking-wide text-subtle">Compactions · {compactions.length}</h3>
        {compactions.length === 0 ? <div className="text-subtle">ยังไม่มี compaction ใน run นี้</div> : null}
        <ul className="space-y-1.5">
          {compactions.map((c) => (
            <li key={c.seq} className="rounded-md border border-line p-2">
              <div className="num flex items-center gap-2">
                <span className={c.kind === "failed" ? "text-error" : c.kind === "fallback_clear" ? "text-warning" : "text-accent"}>{c.kind}</span>
                {c.preTokens !== null ? (
                  <span>
                    {formatTokens(c.preTokens)} → {c.postTokens !== null ? formatTokens(c.postTokens) : "?"}
                  </span>
                ) : null}
                {c.tokensSaved !== null ? <span className="text-muted">−{formatTokens(c.tokensSaved)}</span> : null}
                {c.cleared !== null ? <span className="text-muted">cleared {c.cleared}</span> : null}
                <span className="ml-auto text-[11px] text-subtle">{formatTime(c.ts)}</span>
              </div>
              {c.summary ? <p className="mt-1 line-clamp-4 whitespace-pre-wrap text-muted">{c.summary}</p> : null}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
