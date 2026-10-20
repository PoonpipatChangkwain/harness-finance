"use client";

/**
 * Trade approval takeover (phase-11 §11.2): ApprovalPanel (P08) plus an Edit form for
 * the *basis* of the trade — size % of the PM's size, stop level, target levels. The
 * backend re-derives every number with compute and re-runs the policy (T4); the
 * re-checked policy shows up in the DecisionCard once the run moves on.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { Check, Pencil, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { PolicyList } from "@/components/trade/DecisionCard";
import { Kbd } from "@/components/ui/Kbd";
import { orderLabel, type PolicyResult } from "@/lib/trade-api";
import type { ApprovalView } from "@/stores/run-reducer";
import { tr, useT } from "@/lib/i18n";

export type ApprovalOutcome =
  | { outcome: "approved" | "rejected" }
  | {
      outcome: "edited";
      edits: { final_size_pct?: number; stop_level?: number; target_levels?: number[]; order_price?: number };
    };

const optionalNumber = z
  .string()
  .trim()
  .refine((v) => v === "" || Number.isFinite(Number(v)), { error: () => tr("ต้องเป็นตัวเลข", "Must be a number") });

export const editSchema = z.object({
  sizePct: z
    .string()
    .trim()
    .refine((v) => v === "" || (Number.isFinite(Number(v)) && Number(v) >= 0 && Number(v) <= 100), "0–100"),
  stop: optionalNumber.refine((v) => v === "" || Number(v) > 0, { error: () => tr("ต้องมากกว่า 0", "Must be greater than 0") }),
  // only shown for a pending (limit / stop) order
  orderPrice: optionalNumber.refine((v) => v === "" || Number(v) > 0, { error: () => tr("ต้องมากกว่า 0", "Must be greater than 0") }),
  targets: z
    .string()
    .trim()
    .refine(
      (v) => v === "" || v.split(",").every((t) => t.trim() !== "" && Number.isFinite(Number(t)) && Number(t) > 0),
      { error: () => tr("ราคาคั่นด้วย , เช่น 37, 39", "Prices separated by commas, e.g. 37, 39") },
    )
    .refine((v) => v === "" || v.split(",").length <= 5, { error: () => tr("ไม่เกิน 5 เป้า", "At most 5 targets") }),
});

export type EditForm = z.infer<typeof editSchema>;

/** Form → `edits` body: only the fields the reviewer filled; size % → fraction of the PM size. */
export function buildEdits(form: EditForm): Extract<ApprovalOutcome, { outcome: "edited" }>["edits"] {
  const edits: Extract<ApprovalOutcome, { outcome: "edited" }>["edits"] = {};
  if (form.sizePct.trim() !== "") edits.final_size_pct = Number(form.sizePct) / 100;
  if (form.stop.trim() !== "") edits.stop_level = Number(form.stop);
  if (form.orderPrice.trim() !== "") edits.order_price = Number(form.orderPrice);
  if (form.targets.trim() !== "") edits.target_levels = form.targets.split(",").map((t) => Number(t.trim()));
  return edits;
}

export function asPolicy(results: Record<string, unknown>[]): PolicyResult[] {
  return results.map((r) => ({
    rule_id: String(r.rule_id ?? r.name ?? "rule"),
    passed: Boolean(r.passed),
    severity: r.severity === "warn" ? "warn" : "block",
    message: String(r.message ?? r.detail ?? ""),
    values: (r.values as Record<string, unknown>) ?? {},
  }));
}

const num = (v: unknown) => (typeof v === "number" ? v.toLocaleString("en-US", { maximumSignificantDigits: 6 }) : "–");

export function TradeApprovalPanel({
  approval,
  onResolve,
  busy = false,
}: {
  approval: ApprovalView;
  onResolve: (answer: ApprovalOutcome) => void;
  busy?: boolean;
}) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const p = approval.proposal;
  const pending = orderLabel(typeof p.order === "string" ? p.order : null);
  const form = useForm<EditForm>({
    resolver: zodResolver(editSchema),
    defaultValues: { sizePct: "", stop: "", targets: "", orderPrice: "" },
  });

  useEffect(() => {
    if (editing) return;
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (e.key === "Enter") onResolve({ outcome: "approved" });
      if (e.key === "Escape") onResolve({ outcome: "rejected" });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onResolve, editing]);

  const submit = form.handleSubmit((values) => {
    const edits = buildEdits(values);
    if (!Object.keys(edits).length) {
      form.setError("sizePct", { message: tr("แก้อย่างน้อย 1 ช่อง", "Change at least 1 field") });
      return;
    }
    onResolve({ outcome: "edited", edits });
  });

  const err = form.formState.errors;
  return (
    <div data-testid="trade-approval-panel" className="space-y-2 rounded-lg border border-warning/60 bg-layer-1 p-3">
      <div className="text-[13px] font-medium text-warning">{t("ต้องอนุมัติ", "Approval required")}: {approval.title}</div>
      <div className="num flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-muted">
        <span data-testid="approval-entry">
          {pending ? `${pending} ` : "entry "}
          {num(p.entry)}
          {pending ? ` (${t("ตอนนี้", "now")} ${num(p.market_price)})` : ""}
        </span>
        <span>stop {num(p.stop)}</span>
        <span>targets {Array.isArray(p.targets) ? p.targets.map(num).join(", ") : "–"}</span>
        <span>units {num(p.units)}</span>
        <span>weight {typeof p.position_weight === "number" ? `${(p.position_weight * 100).toFixed(2)}%` : "–"}</span>
      </div>
      <PolicyList results={asPolicy(approval.policyResults)} />
      <p className="text-[11.5px] text-subtle">
        {t(
          "ไม่มีการส่งคำสั่งซื้อขายจริง — อนุมัติแล้วระบบจะบันทึกการตัดสินใจเท่านั้น",
          "No real orders are sent — approving only records the decision",
        )}
      </p>

      {editing ? (
        <form data-testid="approval-edit-form" onSubmit={submit} className="grid gap-2 sm:grid-cols-3">
          <label className="text-[12px] text-muted">
            {t("ขนาด (% ของขนาดที่ PM เสนอ)", "Size (% of the PM's proposed size)")}
            <input
              {...form.register("sizePct")}
              inputMode="decimal"
              placeholder={t("เช่น 50", "e.g. 50")}
              aria-label="size percent"
              className="num mt-0.5 w-full rounded-md border border-line bg-bg px-2 py-1 text-[13px] text-fg"
            />
            {err.sizePct ? <span className="text-[11.5px] text-error">{err.sizePct.message}</span> : null}
          </label>
          {pending ? (
            <label className="text-[12px] text-muted">
              {t("ราคา", "Price")} {pending}
              <input
                {...form.register("orderPrice")}
                inputMode="decimal"
                placeholder={typeof p.entry === "number" ? String(p.entry) : ""}
                aria-label="order price"
                className="num mt-0.5 w-full rounded-md border border-line bg-bg px-2 py-1 text-[13px] text-fg"
              />
              {err.orderPrice ? <span className="text-[11.5px] text-error">{err.orderPrice.message}</span> : null}
            </label>
          ) : null}
          <label className="text-[12px] text-muted">
            {t("Stop (ราคา)", "Stop (price)")}
            <input
              {...form.register("stop")}
              inputMode="decimal"
              placeholder={typeof p.stop === "number" ? String(p.stop) : ""}
              aria-label="stop level"
              className="num mt-0.5 w-full rounded-md border border-line bg-bg px-2 py-1 text-[13px] text-fg"
            />
            {err.stop ? <span className="text-[11.5px] text-error">{err.stop.message}</span> : null}
          </label>
          <label className="text-[12px] text-muted">
            {t("Targets (คั่นด้วย ,)", "Targets (comma-separated)")}
            <input
              {...form.register("targets")}
              placeholder={Array.isArray(p.targets) ? p.targets.join(", ") : ""}
              aria-label="target levels"
              className="num mt-0.5 w-full rounded-md border border-line bg-bg px-2 py-1 text-[13px] text-fg"
            />
            {err.targets ? <span className="text-[11.5px] text-error">{err.targets.message}</span> : null}
          </label>
          <div className="flex gap-2 sm:col-span-3">
            <button
              type="submit"
              disabled={busy}
              className="inline-flex items-center gap-1 rounded-md bg-warning/20 px-3 py-1 text-[13px] text-warning"
            >
              <Check className="size-3.5" /> {t("ส่งการแก้ไข (คำนวณ + ตรวจ policy ใหม่)", "Submit edits (recompute + re-check policy)")}
            </button>
            <button type="button" onClick={() => setEditing(false)} className="rounded-md px-3 py-1 text-[13px] text-muted hover:bg-layer-2">
              {t("ยกเลิก", "Cancel")}
            </button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => onResolve({ outcome: "approved" })}
            className="inline-flex items-center gap-1 rounded-md bg-done/20 px-3 py-1 text-[13px] text-done"
          >
            <Check className="size-3.5" /> Approve <Kbd>Enter</Kbd>
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onResolve({ outcome: "rejected" })}
            className="inline-flex items-center gap-1 rounded-md bg-error/15 px-3 py-1 text-[13px] text-error"
          >
            <X className="size-3.5" /> Reject <Kbd>Esc</Kbd>
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => setEditing(true)}
            className="inline-flex items-center gap-1 rounded-md bg-layer-2 px-3 py-1 text-[13px] text-fg"
          >
            <Pencil className="size-3.5" /> Edit
          </button>
        </div>
      )}
    </div>
  );
}
