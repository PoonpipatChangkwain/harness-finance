"use client";

/**
 * Rebalance proposal of the advisor (phase-13 §13.2): symbol | current | target | Δ |
 * action | qty est. | value, metrics before → after, policy results and Approve / Edit /
 * Reject. Edit changes only target % (+ cash target); the backend recomputes trades and
 * re-runs the policy before saving the plan (T4). Every number is from `rebalance_plan`.
 */

import { clsx } from "clsx";
import { Check, Pencil, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { PolicyList } from "@/components/trade/DecisionCard";
import { asPolicy } from "@/components/trade/TradeApprovalPanel";
import { Kbd } from "@/components/ui/Kbd";
import { formatPct, fractionToPercentText, percentTextToFraction } from "@/lib/portfolio-format";
import type { PlanMetrics, ProposalCard, ProposalRow } from "@/lib/portfolio-api";
import type { ApprovalView } from "@/stores/run-reducer";

import { ProvValue } from "./ProvValue";

export type PlanApproval =
  | { outcome: "approved" | "rejected" }
  | { outcome: "edited"; edits: { target_weights: Record<string, number>; cash_target?: number } };

/** Edited target % per symbol (+ cash) → the approval `edits` body (fractions). */
export function buildTargetEdits(
  rows: ProposalRow[],
  targets: Record<string, string>,
  cashPct: string,
): Extract<PlanApproval, { outcome: "edited" }>["edits"] {
  const target_weights: Record<string, number> = {};
  for (const r of rows) {
    const typed = targets[r.symbol];
    const frac = typed !== undefined ? percentTextToFraction(typed) : undefined;
    target_weights[r.symbol] = frac !== undefined ? Number(frac) : r.target;
  }
  const cash = percentTextToFraction(cashPct);
  return cash !== undefined ? { target_weights, cash_target: Number(cash) } : { target_weights };
}

const ACTION: Record<string, string> = { buy: "Buy", sell: "Sell", hold: "Hold" };

function MetricRow({ label, before, after, pid, format = "pct" }: { label: string; before: number | null; after: number | null; pid?: string; format?: "pct" | "ratio" | "number" }) {
  const fmt = (v: number | null) => (format === "pct" ? formatPct(v, 1) : v === null ? "–" : v.toFixed(format === "ratio" ? 3 : 1));
  return (
    <tr className="border-t border-line/50">
      <td className="py-0.5 text-muted">{label}</td>
      <td className="num py-0.5 text-right">{fmt(before)}</td>
      <td className="px-1 text-subtle">→</td>
      <td className="num py-0.5 text-right">
        {pid && after !== null ? <ProvValue value={after} provenanceId={pid} format={format === "number" ? "number" : format} digits={format === "pct" ? 1 : format === "ratio" ? 3 : 1} /> : fmt(after)}
      </td>
    </tr>
  );
}

function Metrics({ card }: { card: ProposalCard }) {
  const b: PlanMetrics = card.metrics_before;
  const a: PlanMetrics = card.metrics_after;
  const p = card.provenance;
  return (
    <table className="text-[12px]">
      <thead className="text-left text-[11px] text-subtle">
        <tr>
          <th className="font-normal">Metric</th>
          <th className="text-right font-normal">ก่อน</th>
          <th />
          <th className="text-right font-normal">หลัง</th>
        </tr>
      </thead>
      <tbody>
        <MetricRow label="HHI" before={b.hhi} after={a.hhi} pid={p.concentration} format="ratio" />
        <MetricRow label="Top 1" before={b.top1} after={a.top1} pid={p.concentration} />
        <MetricRow label="Effective N" before={b.effective_n} after={a.effective_n} pid={p.concentration} format="number" />
        <MetricRow label="เงินสด" before={b.cash_weight} after={a.cash_weight} pid={p.post_trade} />
        <MetricRow label="Sector ใหญ่สุด" before={b.max_sector} after={a.max_sector} pid={p.sector} />
      </tbody>
    </table>
  );
}

export function ProposalTable({
  approval,
  card: cardProp,
  onResolve,
  busy = false,
  highlight = false,
  footer,
}: {
  /** the pending approval (Approve/Edit/Reject shown) … */
  approval?: ApprovalView | null;
  /** … or a saved plan's card (read-only) */
  card?: ProposalCard | null;
  onResolve?: (answer: PlanApproval) => void;
  busy?: boolean;
  highlight?: boolean;
  footer?: ReactNode;
}) {
  const card = (approval?.proposal as unknown as ProposalCard | undefined) ?? cardProp ?? null;
  const pending = !!approval && approval.outcome === "pending" && !!onResolve;
  const [editing, setEditing] = useState(false);
  const [targets, setTargets] = useState<Record<string, string>>({});
  const [cashPct, setCashPct] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!pending || editing || !onResolve) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)) return;
      if (e.key === "Enter") onResolve({ outcome: "approved" });
      if (e.key === "Escape") onResolve({ outcome: "rejected" });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pending, editing, onResolve]);

  if (!card) return null;
  const pid = card.provenance;

  const startEdit = () => {
    setTargets(Object.fromEntries(card.rows.map((r) => [r.symbol, fractionToPercentText(r.target)])));
    setCashPct(fractionToPercentText(card.cash_target));
    setError(null);
    setEditing(true);
  };
  const submit = () => {
    const bad = Object.entries(targets).find(([, v]) => {
      const n = Number(v.trim().replace(/%$/, ""));
      return v.trim() === "" || !Number.isFinite(n) || n < 0 || n > 100;
    });
    const cash = Number(cashPct.trim().replace(/%$/, ""));
    if (bad) return setError(`target ของ ${bad[0]} ต้องอยู่ระหว่าง 0–100%`);
    if (cashPct.trim() && (!Number.isFinite(cash) || cash < 0 || cash >= 100)) return setError("cash target 0–99%");
    onResolve?.({ outcome: "edited", edits: buildTargetEdits(card.rows, targets, cashPct) });
    setEditing(false);
  };
  const typedTotal =
    Object.values(targets).reduce((s, v) => s + (Number(v.trim().replace(/%$/, "")) || 0), 0) +
    (Number(cashPct.trim().replace(/%$/, "")) || 0);

  return (
    <section
      id="proposal"
      data-testid="proposal-table"
      className={clsx(
        "rounded-md border bg-layer-1 p-3 transition-shadow",
        pending ? "border-warning/60" : "border-line",
        highlight && "ring-2 ring-warning/70",
      )}
    >
      <div className="mb-2 flex flex-wrap items-baseline gap-2">
        <h2 className="text-[13px] font-semibold">{pending ? "ต้องอนุมัติ: " : ""}Rebalance proposal · {card.name}</h2>
        {card.verdict ? (
          <span className="rounded-sm bg-layer-2 px-1.5 text-[11px]">
            {card.verdict} · conf {card.confidence.toFixed(2)}
          </span>
        ) : null}
        <span className="text-[11.5px] text-subtle">
          turnover <ProvValue value={card.turnover} provenanceId={pid.post_trade} format="pct" digits={1} /> · เงินสด{" "}
          <ProvValue value={card.cash_before} provenanceId={pid.trades} /> → <ProvValue value={card.cash_after} provenanceId={pid.post_trade} />{" "}
          {card.base_currency}
        </span>
      </div>
      {card.summary ? <p className="mb-2 text-[12.5px] text-muted">{card.summary}</p> : null}

      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]">
          <thead className="text-left text-[11px] text-subtle">
            <tr>
              <th className="px-1 font-normal">Symbol</th>
              <th className="px-1 text-right font-normal">Current%</th>
              <th className="px-1 text-right font-normal">Target%</th>
              <th className="px-1 text-right font-normal">After%</th>
              <th className="px-1 text-right font-normal">Δ</th>
              <th className="px-1 font-normal">Action</th>
              <th className="px-1 text-right font-normal">Qty est.</th>
              <th className="px-1 text-right font-normal">มูลค่า</th>
            </tr>
          </thead>
          <tbody>
            {card.rows.map((r) => (
              <tr key={r.symbol} data-testid="proposal-row" className="border-t border-line/50" title={r.rationale || undefined}>
                <td className="px-1 py-1 font-medium">{r.symbol}</td>
                <td className="num px-1 text-right">{formatPct(r.current, 1)}</td>
                <td className="num px-1 text-right">
                  {editing ? (
                    <input
                      aria-label={`target ${r.symbol}`}
                      value={targets[r.symbol] ?? ""}
                      onChange={(e) => setTargets((t) => ({ ...t, [r.symbol]: e.target.value }))}
                      className="num w-16 rounded-sm border border-line bg-bg px-1 text-right text-[12.5px] text-fg outline-none focus:border-accent"
                    />
                  ) : (
                    formatPct(r.target, 1)
                  )}
                </td>
                <td className="num px-1 text-right">
                  <ProvValue value={r.after} provenanceId={pid.post_trade} format="pct" digits={1} />
                </td>
                <td className={clsx("num px-1 text-right", r.delta > 0 ? "text-up" : r.delta < 0 ? "text-down" : "text-muted")}>
                  {formatPct(r.delta, 1, true)}
                </td>
                <td className={clsx("px-1", r.action === "buy" ? "text-up" : r.action === "sell" ? "text-down" : "text-muted")}>
                  {ACTION[r.action] ?? r.action}
                </td>
                <td className="num px-1 text-right">
                  <ProvValue value={r.shares} provenanceId={pid.trades} format="number" digits={4} />
                </td>
                <td className="num px-1 text-right">
                  <ProvValue value={r.value} provenanceId={pid.trades} />
                </td>
              </tr>
            ))}
            <tr className="border-t border-line/50 text-muted">
              <td className="px-1 py-1">CASH</td>
              <td className="num px-1 text-right">{formatPct(card.metrics_before.cash_weight, 1)}</td>
              <td className="num px-1 text-right">
                {editing ? (
                  <input
                    aria-label="target cash"
                    value={cashPct}
                    onChange={(e) => setCashPct(e.target.value)}
                    className="num w-16 rounded-sm border border-line bg-bg px-1 text-right text-[12.5px] text-fg outline-none focus:border-accent"
                  />
                ) : (
                  formatPct(card.cash_target, 1)
                )}
              </td>
              <td className="num px-1 text-right">{formatPct(card.metrics_after.cash_weight, 1)}</td>
              <td colSpan={4} />
            </tr>
          </tbody>
        </table>
      </div>
      {editing ? (
        <p className={clsx("num mt-1 text-[11.5px]", Math.abs(typedTotal - 100) > 0.05 ? "text-warning" : "text-subtle")}>
          รวม {typedTotal.toFixed(2)}% {Math.abs(typedTotal - 100) > 0.05 ? "· backend จะ normalize ให้รวมเป็น 100%" : ""}
        </p>
      ) : null}

      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <Metrics card={card} />
        <div>
          <h3 className="mb-1 text-[12px] text-muted">Policy</h3>
          <PolicyList results={asPolicy((approval?.policyResults ?? []) as Record<string, unknown>[])} />
          {card.warnings.length ? (
            <ul className="mt-1 text-[11.5px] text-warning">
              {card.warnings.map((w) => (
                <li key={w}>⚠ {w}</li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>

      {error ? <p className="mt-2 text-[12px] text-error">{error}</p> : null}
      {pending ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {editing ? (
            <>
              <button
                type="button"
                data-testid="submit-plan-edit"
                disabled={busy}
                onClick={submit}
                className="inline-flex items-center gap-1 rounded-md bg-warning/20 px-3 py-1 text-[13px] text-warning"
              >
                <Check className="size-3.5" aria-hidden /> ส่งการแก้ไข (คำนวณเทรด + ตรวจ policy ใหม่)
              </button>
              <button type="button" onClick={() => setEditing(false)} className="rounded-md px-3 py-1 text-[13px] text-muted hover:bg-layer-2">
                ยกเลิก
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                data-testid="approve-plan"
                disabled={busy}
                onClick={() => onResolve?.({ outcome: "approved" })}
                className="inline-flex items-center gap-1 rounded-md bg-done/20 px-3 py-1 text-[13px] text-done"
              >
                <Check className="size-3.5" aria-hidden /> Approve <Kbd>Enter</Kbd>
              </button>
              <button
                type="button"
                data-testid="reject-plan"
                disabled={busy}
                onClick={() => onResolve?.({ outcome: "rejected" })}
                className="inline-flex items-center gap-1 rounded-md bg-error/15 px-3 py-1 text-[13px] text-error"
              >
                <X className="size-3.5" aria-hidden /> Reject <Kbd>Esc</Kbd>
              </button>
              <button
                type="button"
                data-testid="edit-plan"
                disabled={busy}
                onClick={startEdit}
                className="inline-flex items-center gap-1 rounded-md bg-layer-2 px-3 py-1 text-[13px]"
              >
                <Pencil className="size-3.5" aria-hidden /> Edit target%
              </button>
            </>
          )}
          <span className="text-[11.5px] text-subtle">อนุมัติแล้วระบบบันทึกเป็นแผน — ยังไม่แก้ positions จนกว่าจะกด Apply</span>
        </div>
      ) : null}
      {footer}
    </section>
  );
}
