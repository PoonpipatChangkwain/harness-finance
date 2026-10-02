"use client";

/**
 * Approved / applied plans (phase-13 §13.2). "Apply to portfolio" first asks the backend for
 * a dry-run diff and shows it in a confirm dialog; applying writes a new revision (the
 * revision list below shows every change of the book and why).
 */

import { clsx } from "clsx";
import { ChevronRight, History, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ApiError } from "@/lib/api";
import { formatRelative } from "@/lib/format";
import { formatPct, formatQty } from "@/lib/portfolio-format";
import { portfolioApi, type ApplyDiff, type PlanRecord, type RevisionRecord } from "@/lib/portfolio-api";

import { ProposalTable } from "./ProposalTable";

const REASON: Record<string, string> = { created: "สร้าง", edit: "แก้ไข", import: "Import CSV", apply_plan: "Apply แผน" };

function ApplyDialog({
  plan,
  diff,
  stale,
  busy,
  error,
  onConfirm,
  onClose,
}: {
  plan: PlanRecord;
  diff: ApplyDiff;
  stale: boolean;
  busy: boolean;
  error: string | null;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <div role="dialog" aria-modal="true" aria-label="Apply plan" className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4">
      <div data-testid="apply-dialog" className="w-full max-w-lg rounded-lg border border-line-strong bg-layer-1 shadow-xl">
        <div className="flex items-center border-b border-line px-4 py-2">
          <h2 className="text-[14px] font-semibold">Apply แผนเข้าพอร์ต?</h2>
          <span className="flex-1" />
          <button type="button" aria-label="ปิด" onClick={onClose} className="rounded-md p-1 text-muted hover:bg-layer-2">
            <X className="size-4" aria-hidden />
          </button>
        </div>
        <div className="space-y-2 p-4 text-[12.5px]">
          <p className="text-muted">
            จำนวนหุ้นจะเปลี่ยนตามแผน {plan.id} (ไม่มีการส่งคำสั่งซื้อขายจริง) และบันทึกเป็น revision ใหม่
          </p>
          {stale ? (
            <p className="rounded-md bg-warning/15 p-2 text-warning">
              พอร์ตเปลี่ยนไปหลังจากสร้างแผนนี้ — แนะนำให้รัน advisor ใหม่ หรือกด Apply อีกครั้งเพื่อบังคับ
            </p>
          ) : null}
          <table data-testid="apply-diff" className="num w-full">
            <thead className="text-left text-[11px] text-subtle">
              <tr>
                <th className="font-normal">Symbol</th>
                <th className="text-right font-normal">ก่อน</th>
                <th className="text-right font-normal">หลัง</th>
                <th className="text-right font-normal">เปลี่ยน</th>
              </tr>
            </thead>
            <tbody>
              {diff.positions.map((d) => (
                <tr key={d.symbol} className="border-t border-line/50">
                  <td className="py-0.5 font-sans">{d.symbol}</td>
                  <td className="text-right">{formatQty(d.before)}</td>
                  <td className="text-right">{formatQty(d.after)}</td>
                  <td className={clsx("text-right", Number(d.change) > 0 ? "text-up" : "text-down")}>
                    {Number(d.change) > 0 ? "+" : ""}
                    {formatQty(d.change)}
                  </td>
                </tr>
              ))}
              <tr className="border-t border-line/50 text-muted">
                <td className="py-0.5 font-sans">เงินสด</td>
                <td className="text-right">{formatQty(diff.cash_before)}</td>
                <td className="text-right">{formatQty(diff.cash_after)}</td>
                <td />
              </tr>
            </tbody>
          </table>
          {diff.positions.length === 0 ? <p className="text-subtle">แผนนี้ไม่มีการเปลี่ยนจำนวนหุ้น (ทุกตัว hold)</p> : null}
          {error ? <p className="text-error">{error}</p> : null}
        </div>
        <div className="flex justify-end gap-2 border-t border-line px-4 py-2">
          <button type="button" onClick={onClose} className="rounded-md px-3 py-1 text-[13px] text-muted hover:bg-layer-2">
            ยกเลิก
          </button>
          <button
            type="button"
            data-testid="confirm-apply"
            disabled={busy}
            onClick={onConfirm}
            className="rounded-md bg-accent px-3 py-1 text-[13px] text-bg disabled:opacity-50"
          >
            {stale ? "บังคับ Apply" : "Apply"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function PlansHistory({ portfolioId, revision, onApplied }: { portfolioId: string; revision: number; onApplied: () => void }) {
  const [plans, setPlans] = useState<PlanRecord[] | null>(null);
  const [revisions, setRevisions] = useState<RevisionRecord[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [showRevisions, setShowRevisions] = useState(false);
  const [applying, setApplying] = useState<{ plan: PlanRecord; diff: ApplyDiff; stale: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    portfolioApi
      .plans(portfolioId)
      .then((r) => setPlans(r.plans))
      .catch(() => setPlans([]));
    portfolioApi
      .revisions(portfolioId)
      .then((r) => setRevisions(r.revisions))
      .catch(() => setRevisions([]));
  }, [portfolioId]);
  useEffect(load, [load, revision]);

  const startApply = async (plan: PlanRecord) => {
    setError(null);
    setBusy(true);
    try {
      const dry = await portfolioApi.applyPlan(portfolioId, plan.id, { dryRun: true });
      setApplying({ plan, diff: dry.diff, stale: false });
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && /changed/.test(err.message)) {
        try {
          const dry = await portfolioApi.applyPlan(portfolioId, plan.id, { dryRun: true, force: true });
          setApplying({ plan, diff: dry.diff, stale: true });
        } catch (inner) {
          setError(inner instanceof Error ? inner.message : String(inner));
        }
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setBusy(false);
    }
  };
  const confirm = async () => {
    if (!applying) return;
    setBusy(true);
    try {
      await portfolioApi.applyPlan(portfolioId, applying.plan.id, { force: applying.stale });
      setApplying(null);
      onApplied();
      load();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && !applying.stale && /changed/.test(err.message)) {
        setApplying({ ...applying, stale: true });
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <section data-testid="plans-history" className="rounded-md border border-line bg-layer-1 p-3">
      <div className="mb-2 flex items-center gap-2">
        <h2 className="text-[13px] font-semibold">แผนที่อนุมัติแล้ว</h2>
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => setShowRevisions((v) => !v)}
          className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[12px] text-muted hover:bg-layer-2"
        >
          <History className="size-3.5" aria-hidden /> Revisions ({revisions?.length ?? "…"})
        </button>
      </div>
      {error && !applying ? <p className="mb-1 text-[12px] text-error">{error}</p> : null}
      {plans === null ? <p className="text-[12px] text-subtle">กำลังโหลด…</p> : null}
      {plans?.length === 0 ? <p className="text-[12px] text-subtle">ยังไม่มีแผน · กด “เสนอ rebalance” ใน AI Advisor</p> : null}
      <ul className="space-y-1">
        {plans?.map((p) => {
          const card = p.proposal.card;
          const trades = card?.rows.filter((r) => r.action !== "hold").length ?? 0;
          return (
            <li key={p.id} data-testid="plan-item" className="rounded-md border border-line/60">
              <div className="flex flex-wrap items-center gap-2 px-2 py-1.5 text-[12.5px]">
                <button type="button" aria-expanded={open === p.id} onClick={() => setOpen(open === p.id ? null : p.id)} className="inline-flex items-center gap-1">
                  <ChevronRight className={clsx("size-3.5 text-subtle", open === p.id && "rotate-90")} aria-hidden />
                  <span className="num">{p.id.slice(-8)}</span>
                </button>
                <span className={clsx("rounded-sm px-1.5 text-[11px]", p.status === "applied" ? "bg-done/15 text-done" : "bg-accent-muted text-accent")}>
                  {p.status}
                </span>
                {p.response?.outcome === "edited" ? <span className="text-[11px] text-warning">แก้ target</span> : null}
                <span className="text-muted">
                  {trades} trades · turnover {formatPct(card?.turnover, 1)}
                </span>
                <span className="text-[11px] text-subtle">{formatRelative(p.approved_at ?? p.created_at)}</span>
                <span className="flex-1" />
                {p.status === "approved" ? (
                  <button
                    type="button"
                    data-testid="apply-plan"
                    disabled={busy}
                    onClick={() => void startApply(p)}
                    className="rounded-md bg-accent px-2 py-0.5 text-[12px] text-bg disabled:opacity-50"
                  >
                    Apply to portfolio
                  </button>
                ) : p.applied_revision_id ? (
                  <span className="num text-[11px] text-subtle">rev {p.applied_revision_id.slice(-8)}</span>
                ) : null}
              </div>
              {open === p.id && card ? (
                <div className="border-t border-line/60 p-2">
                  <ProposalTable card={card} />
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
      {showRevisions ? (
        <table data-testid="revisions" className="mt-2 w-full text-[12px]">
          <tbody>
            {revisions?.map((r) => (
              <tr key={r.id} className="border-t border-line/50">
                <td className="num py-0.5 text-subtle">{r.id.slice(-8)}</td>
                <td className="py-0.5">{REASON[r.reason] ?? r.reason}</td>
                <td className="num py-0.5 text-muted">{r.plan_id ? `plan ${r.plan_id.slice(-8)}` : ""}</td>
                <td className="py-0.5 text-right text-subtle">{formatRelative(r.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {applying ? (
        <ApplyDialog
          plan={applying.plan}
          diff={applying.diff}
          stale={applying.stale}
          busy={busy}
          error={error}
          onConfirm={() => void confirm()}
          onClose={() => {
            setApplying(null);
            setError(null);
          }}
        />
      ) : null}
    </section>
  );
}
