"use client";

/**
 * How an approved trade ended — ticked by the trader (the system sends no orders, so only
 * they know). TP/SL use the decision's own levels; a manual or time-stop exit needs the exit
 * price. The backend computes R and writes a lesson the next run on this symbol reads.
 * Re-ticking overwrites (a mistake can be corrected).
 */

import { clsx } from "clsx";
import { useState } from "react";

import type { DistilledLesson, OutcomeKind, TradeOutcomeBody, TradeResult } from "@/lib/trade-api";
import { tr, useT } from "@/lib/i18n";

const num = (v: unknown) =>
  typeof v === "number" ? v.toLocaleString("en-US", { maximumSignificantDigits: 6 }) : "–";

export function TradeOutcomePanel({
  result,
  onSettle,
}: {
  result: TradeResult;
  /** error text, or null / the lessons the backend distilled (P17) on success */
  onSettle: (body: TradeOutcomeBody) => Promise<string | null | { distilled: DistilledLesson[] }>;
}) {
  const t = useT();
  const card = result.card ?? {};
  const targets = Array.isArray(card.targets) ? (card.targets as number[]) : [];
  const [editing, setEditing] = useState(result.outcome === null);
  const [exitKind, setExitKind] = useState<OutcomeKind | null>(null);
  const [price, setPrice] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [distilled, setDistilled] = useState<DistilledLesson[] | null>(null);

  const submit = async (body: TradeOutcomeBody) => {
    setBusy(true);
    setError(null);
    const res = await onSettle({ ...body, note: note.trim() });
    setBusy(false);
    if (typeof res === "string") setError(res);
    else {
      setDistilled(res?.distilled ?? null);
      setEditing(false);
      setExitKind(null);
      setPrice("");
    }
  };
  const btn = "rounded-md border border-line px-2 py-1 text-[12.5px] hover:bg-layer-2 disabled:opacity-50";
  const outcome = result.outcome;
  const r = outcome?.r_multiple;

  return (
    <section data-testid="trade-outcome" className="space-y-2 rounded-lg border border-line bg-layer-1 p-3">
      <h3 className="text-[12px] font-medium uppercase tracking-wide text-subtle">{t("ผลการเทรด", "Trade outcome")}</h3>

      {outcome && !editing ? (
        <div className="space-y-1.5 text-[13px]">
          <p>
            <b>{outcome.label}</b>
            {outcome.exit_price !== null ? <span className="num"> · exit {num(outcome.exit_price)}</span> : null}
            {r !== null && r !== undefined ? (
              <span data-testid="r-multiple" className={clsx("num ml-2 font-semibold", r >= 0 ? "text-up" : "text-down")}>
                {r >= 0 ? "+" : ""}
                {r.toFixed(2)}R
              </span>
            ) : null}
            <button type="button" className="ml-3 text-[12px] text-accent hover:underline" onClick={() => setEditing(true)}>
              {t("แก้ไข", "Edit")}
            </button>
          </p>
          {outcome.note ? <p className="text-[12.5px] text-muted">{t("โน้ต", "Note")}: {outcome.note}</p> : null}
          {result.reflection ? (
            <p data-testid="reflection" className="rounded-md bg-layer-2 p-2 text-[12.5px]">
              <span className="text-subtle">
                {t(`บทเรียน (ส่งต่อให้รอบถัดไปของ ${result.symbol}): `, `Lesson (carried into the next ${result.symbol} run): `)}
              </span>
              {result.reflection}
            </p>
          ) : result.reflection_error ? (
            <p className="text-[12px] text-warning">
              {t("เขียนบทเรียนไม่สำเร็จ", "Could not write the lesson")}: {result.reflection_error} —{" "}
              {t("กดแก้ไขแล้วบันทึกอีกครั้งเพื่อลองใหม่", "press Edit and save again to retry")}
            </p>
          ) : null}
        </div>
      ) : (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-1.5">
            {targets.map((t, i) => (
              <button key={i} type="button" disabled={busy} className={clsx(btn, "text-up")}
                onClick={() => void submit({ outcome: "tp", target_index: i })}>
                TP{i + 1} @ {num(t)}
              </button>
            ))}
            <button type="button" disabled={busy || typeof card.stop !== "number"} className={clsx(btn, "text-down")}
              onClick={() => void submit({ outcome: "sl" })}>
              SL @ {num(card.stop)}
            </button>
            <button type="button" disabled={busy} className={clsx(btn, exitKind === "manual_exit" && "bg-layer-2")}
              onClick={() => setExitKind("manual_exit")}>
              {t("ออกเอง…", "Manual exit…")}
            </button>
            <button type="button" disabled={busy} className={clsx(btn, exitKind === "time_stop" && "bg-layer-2")}
              onClick={() => setExitKind("time_stop")}>
              {t("หมดเวลา (time stop)…", "Time stop…")}
            </button>
            <button type="button" disabled={busy} className={btn} onClick={() => void submit({ outcome: "not_filled" })}>
              {t("ไม่ได้เข้า", "Not filled")}
            </button>
            <button type="button" disabled={busy} className={btn} onClick={() => void submit({ outcome: "cancelled" })}>
              {t("ยกเลิก", "Cancelled")}
            </button>
          </div>
          {exitKind ? (
            <form
              className="flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                const p = Number(price);
                if (!Number.isFinite(p) || p <= 0) return setError(tr("ใส่ราคาออกให้ถูกต้อง", "Enter a valid exit price"));
                void submit({ outcome: exitKind, exit_price: p });
              }}
            >
              <input
                aria-label="exit price"
                inputMode="decimal"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                placeholder={t("ราคาที่ออก", "Exit price")}
                className="num w-32 rounded-md border border-line bg-bg px-2 py-1 text-[12.5px]"
              />
              <button type="submit" disabled={busy} className={btn}>
                {t("บันทึก", "Save")}
              </button>
            </form>
          ) : null}
          <input
            aria-label="note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={t("โน้ต (ไม่บังคับ) เช่น ข่าวออกแล้วกระชาก, เลื่อน stop ตาม", "Note (optional), e.g. spiked on the news, trailed the stop")}
            className="w-full rounded-md border border-line bg-bg px-2 py-1 text-[12.5px]"
          />
          {busy ? <p className="text-[12px] text-muted">{t("กำลังบันทึกและเขียนบทเรียน…", "Saving and writing the lesson…")}</p> : null}
          {outcome ? (
            <button type="button" className="text-[12px] text-muted hover:underline" onClick={() => setEditing(false)}>
              {t("ยกเลิกการแก้ไข", "Cancel editing")}
            </button>
          ) : null}
        </div>
      )}
      {distilled?.length && !editing ? (
        <p data-testid="distilled-note" className="text-[12px] text-subtle" title={distilled.map((d) => d.title).join("\n")}>
          {t(`บันทึกบทเรียน ${distilled.length} ข้อลง ReasoningBank`, `Saved ${distilled.length} lesson(s) to the ReasoningBank`)}
        </p>
      ) : null}
      {error ? <p className="text-[12px] text-error">{error}</p> : null}
    </section>
  );
}
