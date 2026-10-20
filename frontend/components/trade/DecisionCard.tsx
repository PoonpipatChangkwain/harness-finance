"use client";

/**
 * Decision card (ui-spec §5, phase-11 §11.2): rating + verdict badges, confidence bar,
 * entry / stop / targets / R:R / size — every number with a CitationChip to its
 * compute provenance — the short reason and the policy results (✓ / ✗ / !).
 * Numbers are shown exactly as the backend computed them (F1): only formatted here.
 */

import { clsx } from "clsx";
import { useEffect, useState } from "react";

import { CitationChip, loadProvenance } from "@/components/conversation/CitationChip";
import type { ProvenanceRecord } from "@/lib/api";
import { intlLocale, useT } from "@/lib/i18n";
import {
  answeredApproval,
  orderLabel,
  type PolicyResult,
  type TradeRunDetail,
  type TradeSizing,
} from "@/lib/trade-api";

type Loader = (pid: string) => Promise<ProvenanceRecord | null>;

export interface SizingCitations {
  entry: string | null;
  order: string | null;
  stop: string | null;
  riskReward: (string | null)[];
  units: string | null;
  size: string | null;
  timeStop: string | null;
}

/** Which compute record backs which number of a `size_trade` result (by its calculation). */
export function mapCitations(records: (ProvenanceRecord | null)[], snapshotId: string | null): SizingCitations {
  const out: SizingCitations = {
    entry: snapshotId,
    order: null,
    stop: null,
    riskReward: [],
    units: null,
    size: null,
    timeStop: null,
  };
  for (const r of records) {
    const calc = r?.calculation ?? "";
    if (!r) continue;
    if (calc.startsWith("compute.risk.stop_from_atr") || calc.startsWith("compute.trade.stop_level")) out.stop = r.id;
    else if (calc.startsWith("compute.risk.risk_reward")) out.riskReward.push(r.id);
    else if (calc.startsWith("compute.risk.position_size")) out.units = r.id;
    else if (calc.startsWith("compute.trade.size")) out.size = r.id;
    else if (calc.startsWith("compute.trade.time_stop")) out.timeStop = r.id;
    else if (calc.startsWith("compute.trade.order@")) out.order = r.id;
  }
  out.units ??= out.size;
  return out;
}

function useCitations(ids: string[], snapshotId: string | null, load: Loader): SizingCitations | null {
  const [citations, setCitations] = useState<SizingCitations | null>(null);
  const key = ids.join(",");
  useEffect(() => {
    let alive = true;
    const list = key ? key.split(",") : [];
    Promise.all(list.map((id) => load(id))).then((records) => {
      if (alive) setCitations(mapCitations(records, snapshotId));
    });
    return () => {
      alive = false;
    };
  }, [key, snapshotId, load]);
  return citations;
}

const num = (v: number | null | undefined, digits = 4) =>
  v === null || v === undefined ? "–" : v.toLocaleString("en-US", { maximumSignificantDigits: Math.max(digits, 6) });
const pct = (v: number | null | undefined) => (v === null || v === undefined ? "–" : `${(v * 100).toFixed(2)}%`);

const VERDICT_CLASS: Record<string, string> = {
  APPROVE: "bg-[color-mix(in_srgb,var(--fh-verdict-approve)_18%,transparent)] text-[var(--fh-verdict-approve)]",
  REVIEW: "bg-[color-mix(in_srgb,var(--fh-verdict-review)_18%,transparent)] text-[var(--fh-verdict-review)]",
  REJECT: "bg-[color-mix(in_srgb,var(--fh-verdict-reject)_18%,transparent)] text-[var(--fh-verdict-reject)]",
};

export function VerdictBadge({ verdict, confidence }: { verdict: string; confidence?: number }) {
  return (
    <span
      data-testid="verdict-badge"
      data-verdict={verdict}
      className={clsx("num rounded-sm px-1.5 py-0.5 text-[12px] font-semibold", VERDICT_CLASS[verdict] ?? "bg-layer-2 text-muted")}
    >
      {verdict}
      {confidence !== undefined ? ` (${confidence.toFixed(2)})` : ""}
    </span>
  );
}

export function PolicyList({ results }: { results: PolicyResult[] }) {
  const t = useT();
  if (!results.length) return <p className="text-[12.5px] text-subtle">{t("ยังไม่ได้ตรวจ policy", "Policy not checked yet")}</p>;
  return (
    <ul data-testid="policy-results" className="space-y-0.5 text-[12.5px]">
      {results.map((r) => {
        const mark = r.passed ? "✓" : r.severity === "block" ? "✗" : "!";
        return (
          <li
            key={r.rule_id}
            data-testid="policy-result"
            data-passed={r.passed}
            className={clsx(r.passed ? "text-done" : r.severity === "block" ? "text-error" : "text-warning")}
          >
            <span className="num mr-1">{mark}</span>
            <span className="font-medium">{r.rule_id}</span>
            <span className="text-muted"> · {r.message}</span>
          </li>
        );
      })}
    </ul>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] uppercase tracking-wide text-subtle">{label}</div>
      <div className="num text-[13.5px] text-fg">{children}</div>
    </div>
  );
}

export function DecisionCard({
  detail,
  runId,
  load = loadProvenance,
}: {
  detail: TradeRunDetail;
  runId: string;
  load?: Loader;
}) {
  const t = useT();
  const decision = detail.decision;
  const approval = answeredApproval(detail);
  // latest numbers: re-priced at approval > re-priced at the live quote > the PM's snapshot sizing
  const approved = approval && approval.outcome !== "rejected" && approval.sizing ? approval : null;
  const edited = approved?.outcome === "edited" ? approved : null;
  const live = detail.live?.refreshed ? detail.live : null;
  const sizing: TradeSizing | undefined = approved?.sizing ?? live?.sizing ?? decision?.sizing;
  const ids = (approved?.provenance_ids ?? live?.provenance_ids ?? decision?.provenance_ids ?? []) as string[];
  const quote = approved ? (approved.quote ?? null) : (live?.quote ?? null);
  const snapshotId = quote?.provenance_ids[0] ?? detail.market_snapshot?.provenance_ids?.[0] ?? null;
  const citations = useCitations(ids, snapshotId, load);
  if (!decision || !sizing) return null;

  const policy = approved?.policy ?? live?.policy ?? detail.policy;
  let marker = 0;
  const cite = (pid: string | null | undefined) =>
    pid ? <CitationChip marker={++marker} provenanceId={pid} runId={runId} load={load} /> : null;
  const currency = sizing.currency ?? detail.instrument?.currency ?? "";
  const invalidation = String(detail.trade_proposal?.proposal?.invalidation ?? "").trim();
  const pending = orderLabel(sizing.order);
  const when = (iso: string) => new Date(iso).toLocaleString(intlLocale(), { dateStyle: "short", timeStyle: "short" });

  return (
    <section data-testid="decision-card" className="space-y-3 rounded-lg border border-line bg-layer-1 p-3">
      <header className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-semibold">{detail.instrument?.display ?? detail.instrument?.canonical}</span>
        <span data-testid="rating-badge" className="rounded-sm bg-accent-muted px-1.5 py-0.5 text-[12px] font-semibold text-accent">
          {decision.rating}
        </span>
        <VerdictBadge verdict={decision.verdict} />
        <span className="flex items-center gap-1 text-[11.5px] text-subtle" title="confidence">
          <span className="relative h-1.5 w-20 overflow-hidden rounded-full bg-layer-2">
            <span
              data-testid="confidence-bar"
              className="absolute inset-y-0 left-0 rounded-full bg-accent"
              style={{ width: `${Math.round(decision.decision.confidence * 100)}%` }}
            />
          </span>
          <span className="num">{decision.decision.confidence.toFixed(2)}</span>
        </span>
        <span className="ml-auto text-[11.5px] uppercase text-muted">{sizing.side}</span>
        {edited ? (
          <span data-testid="edited-note" className="rounded-sm bg-warning/15 px-1.5 py-0.5 text-[11.5px] text-warning">
            {t("แก้ไขโดยผู้อนุมัติ", "Edited by approver")} · size {pct(edited.final_size_pct)}
          </span>
        ) : null}
      </header>

      <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
        <Field label={pending ? "Pending entry" : "Entry"}>
          {pending ? (
            <span
              data-testid="order-badge"
              data-valid={!sizing.order_error}
              className={clsx(
                "mr-1 rounded-sm px-1 py-0.5 text-[11px] font-semibold",
                sizing.order_error ? "bg-error/15 text-error" : "bg-accent-muted text-accent",
              )}
            >
              {pending}
            </span>
          ) : null}
          {num(sizing.entry)}
          {pending ? cite(citations?.order) : cite(citations?.entry)}
          {pending ? (
            <div data-testid="order-detail" className="text-[11px] text-subtle">
              {t("ราคาตอนนี้", "Now")} {num(sizing.market_price)}
              {cite(citations?.entry)}
              {sizing.order_distance_atr !== null && sizing.order_distance_atr !== undefined
                ? ` · ${t("ห่าง", "distance")} ${sizing.order_distance_atr.toFixed(2)} ATR`
                : ""}
              {sizing.order_expires_at ? ` · ${t("หมดอายุ", "expires")} ${when(sizing.order_expires_at)}` : ""}
            </div>
          ) : quote ? (
            <div data-testid="entry-live" className="text-[11px] text-subtle">
              {t("ราคาสด", "Live price")}
              {quote.ts ? ` ${new Date(quote.ts).toLocaleTimeString(intlLocale(), { hour12: false })}` : ""}
              {detail.market_snapshot?.close !== undefined ? ` · snapshot ${num(detail.market_snapshot.close)}` : ""}
            </div>
          ) : null}
        </Field>
        <Field label="Stop">
          {sizing.stop === null ? <span className="text-error">{t("ไม่มี", "None")}</span> : num(sizing.stop)}
          {cite(citations?.stop)}
        </Field>
        <Field label="Targets">
          {(sizing.targets ?? []).length
            ? (sizing.targets ?? []).map((t, i) => (
                <span key={i} className="mr-1">
                  {num(t)}
                </span>
              ))
            : "–"}
        </Field>
        <Field label="R:R">
          {(sizing.risk_reward ?? []).length
            ? (sizing.risk_reward ?? []).map((rr, i) => (
                <span key={i} className="mr-1">
                  {rr === null ? "–" : rr.toFixed(2)}
                  {cite(citations?.riskReward[i])}
                </span>
              ))
            : "–"}
        </Field>
        <Field label="Size">
          {num(sizing.units)} units
          {cite(citations?.units)}
        </Field>
        <Field label="Notional">
          {num(sizing.notional)} {currency}
        </Field>
        <Field label="Weight">
          {pct(sizing.position_weight)}
          {cite(citations?.size)}
        </Field>
        <Field label="Risk">{pct(sizing.risk_fraction)} of equity</Field>
        {sizing.time_stop_bars ? (
          <Field label="Time stop">
            <span data-testid="time-stop">
              {sizing.time_stop_bars} × {sizing.interval ?? ""}
              {cite(citations?.timeStop)}
              {sizing.time_stop_from === "fill" ? (
                <span className="block text-[11px] text-subtle">{t("นับจากตอนได้เข้า", "counted from the fill")}</span>
              ) : sizing.time_stop_at ? (
                <span className="block text-[11px] text-subtle">{t("ภายใน", "by")} {when(sizing.time_stop_at)}</span>
              ) : null}
            </span>
          </Field>
        ) : null}
      </div>
      {invalidation ? (
        <p data-testid="invalidation" className="text-[12.5px]">
          <span className="text-subtle">Invalidation: </span>
          {invalidation}
        </p>
      ) : null}

      {decision.decision.reason || decision.summary ? (
        <p className="text-[12.5px] text-muted">{decision.summary || decision.decision.reason}</p>
      ) : null}
      {(sizing.notes ?? []).length ? (
        <p className="text-[11.5px] text-subtle">{(sizing.notes ?? []).join(" · ")}</p>
      ) : null}

      <div>
        <h4 className="mb-1 text-[11px] font-medium uppercase tracking-wide text-subtle">
          Policy {policy?.blocked ? <span className="text-error">· blocked</span> : null}
        </h4>
        <PolicyList results={policy?.results ?? []} />
      </div>
    </section>
  );
}
