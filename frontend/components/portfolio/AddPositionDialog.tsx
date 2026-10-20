"use client";

/**
 * Guided "add position" dialog (phase-16 §16.3, ui-spec §6).
 * 1. pick the symbol (SymbolSearch) → market / currency badge + last price (`/market/{s}/quote`)
 * 2. "คุณเห็นอะไรในแอปโบรกเกอร์?" — one of 6 entry modes, two inputs, a กำไร / ขาดทุน toggle
 *    instead of typing a minus sign; the last mode is remembered (localStorage)
 * A live preview (`POST …/positions/preview`, debounced, stale answers dropped) shows what the
 * backend computes — quantity, avg cost, price, value, P&L — all with provenance (F1: the
 * dialog never computes). `เพิ่มอีกตัว` collects rows; `บันทึกทั้งหมด` hands them to `onSave`
 * (the parent merges them into the book and PUTs); a 422 maps back to the list by index.
 */

import { clsx } from "clsx";
import { Pencil, Plus, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { AssetBadge, SymbolSearch } from "@/components/trade/SymbolSearch";
import type { SymbolInfo } from "@/lib/api";
import {
  API_FIELD,
  ENTRY_MODES,
  MODE_SPEC,
  buildPositions,
  emptyRow,
  fieldLabel,
  groupErrors,
  isGuidedMode,
  modeLabel,
  moneyUnit,
  validateRows,
  type DraftRow,
  type GuidedMode,
} from "@/lib/holdings";
import { useT } from "@/lib/i18n";
import {
  entryErrors,
  portfolioApi,
  type EntryError,
  type PositionInput,
  type PositionsPreview,
  type PreviewRow,
  type PV,
  type QuoteOut,
} from "@/lib/portfolio-api";
import { MARKET_LABEL } from "@/lib/portfolio-format";

import { ProvValue } from "./ProvValue";

type SymbolSearchFn = Parameters<typeof SymbolSearch>[0]["search"];

export interface AddPositionApi {
  preview: (id: string | null, positions: PositionInput[]) => Promise<PositionsPreview>;
  quote?: (symbol: string) => Promise<QuoteOut>;
}

const defaultApi: AddPositionApi = { preview: portfolioApi.positionsPreview, quote: portfolioApi.quote };

const MODE_KEY = "fh-portfolio-entry-mode";

export function loadLastMode(): GuidedMode {
  try {
    const v = localStorage.getItem(MODE_KEY);
    return isGuidedMode(v) ? v : "qty_cost";
  } catch {
    return "qty_cost";
  }
}

function rememberMode(mode: GuidedMode) {
  try {
    localStorage.setItem(MODE_KEY, mode);
  } catch {
    // storage blocked: not remembered
  }
}

export interface AddForm {
  symbol: SymbolInfo | null;
  mode: GuidedMode;
  /** quantity or market value, as typed */
  first: string;
  /** the second number of the mode, without a sign */
  second: string;
  /** gain/loss toggle of the signed modes */
  loss: boolean;
}

/** The form as a draft row (the loss toggle becomes the sign of pnl_pct / pnl_amount). */
export function formDraft(f: AddForm): DraftRow {
  const spec = MODE_SPEC[f.mode];
  const second = f.second.trim();
  const signed = spec.signed && f.loss && second ? `-${second}` : second;
  return { ...emptyRow(f.mode), symbol: f.symbol?.canonical ?? "", [spec.first.field]: f.first, [spec.second.field]: signed };
}

/** The `PositionInput` the dialog sends for this form. */
export const formPosition = (f: AddForm): PositionInput => buildPositions([formDraft(f)])[0]!;

interface Item {
  key: string;
  form: AddForm;
  position: PositionInput;
  preview: PreviewRow | null;
  errors: EntryError[];
}

const blankForm = (mode: GuidedMode): AddForm => ({ symbol: null, mode, first: "", second: "", loss: false });

const inputCls =
  "num w-full rounded-md border border-line bg-bg px-2 py-1.5 text-right text-[13px] text-fg outline-none placeholder:text-subtle focus:border-accent";

/** Errors on one input: field errors of the backend / local check; `warn` = status ok (e.g. no price). */
function FieldErrors({ errors, warn, testId }: { errors: EntryError[]; warn?: boolean; testId: string }) {
  if (!errors.length) return null;
  return (
    <p data-testid={testId} className={clsx("mt-0.5 text-[11.5px]", warn ? "text-warning" : "text-error")}>
      {errors.map((e) => e.message).join(" · ")}
    </p>
  );
}

/** What the backend computed for one row (every number with provenance). */
export function PreviewSummary({ row, compact = false }: { row: PreviewRow; compact?: boolean }) {
  const t = useT();
  const unit = moneyUnit(row.currency);
  const qty = row.quantity !== null ? Number(row.quantity) : null;
  const avg = row.avg_cost !== null ? Number(row.avg_cost) : null;
  if (compact) {
    return (
      <span className="num inline-flex flex-wrap items-center gap-x-2 text-[12px]">
        <ProvValue value={qty} provenanceId={row.provenance_id} format="number" digits={6} />
        <span className="text-subtle">@</span>
        <ProvValue value={avg} provenanceId={row.provenance_id} format="number" digits={4} />
        <span className="text-subtle">·</span>
        <ProvValue pv={row.market_value} suffix={` ${unit}`} />
        <ProvValue pv={row.unrealized_pnl} format="signed" colored />
        <ProvValue pv={row.unrealized_pnl_pct} format="signedPct" colored />
      </span>
    );
  }
  const cell = (label: string, body: React.ReactNode, testId: string) => (
    <div data-testid={testId}>
      <div className="text-[11px] text-subtle">{label}</div>
      <div className="text-[13px]">{body}</div>
    </div>
  );
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
      {cell(t("จำนวน", "Quantity"), <ProvValue value={qty} provenanceId={row.provenance_id} format="number" digits={6} />, "pv-quantity")}
      {cell(
        t(`ต้นทุนเฉลี่ย (${unit})`, `Avg cost (${unit})`),
        <ProvValue value={avg} provenanceId={row.provenance_id} format="number" digits={4} />,
        "pv-avg-cost",
      )}
      {cell(
        t(`ราคาล่าสุด (${unit})`, `Last price (${unit})`),
        <>
          <ProvValue pv={row.price} />
          {row.price_as_of ? <span className="num ml-1 text-[11px] text-subtle">{row.price_as_of.slice(0, 10)}</span> : null}
        </>,
        "pv-price",
      )}
      {cell(t(`มูลค่า (${unit})`, `Value (${unit})`), <ProvValue pv={row.market_value} />, "pv-value")}
      {cell(t(`ต้นทุนรวม (${unit})`, `Total cost (${unit})`), <ProvValue pv={row.cost_basis} />, "pv-cost-basis")}
      {cell(
        t("กำไร/ขาดทุน", "P/L"),
        <span className="inline-flex flex-wrap gap-x-1.5">
          <ProvValue pv={row.unrealized_pnl} format="signed" colored />
          <ProvValue pv={row.unrealized_pnl_pct} format="signedPct" colored />
        </span>,
        "pv-pnl",
      )}
    </div>
  );
}

export function AddPositionDialog({
  portfolioId,
  onClose,
  onSave,
  onPercentMode,
  api = defaultApi,
  search,
  previewDelayMs = 400,
}: {
  /** null → the default book's `/portfolio/positions/preview` */
  portfolioId: string | null;
  onClose: () => void;
  /** merge the new rows into the book and PUT; resolves to errors indexed by the new rows (422) or null when saved */
  onSave: (rows: PositionInput[]) => Promise<EntryError[] | null>;
  /** "กรอกแค่ % น้ำหนัก" → the %-only mode of the table */
  onPercentMode?: () => void;
  api?: AddPositionApi;
  search?: SymbolSearchFn;
  previewDelayMs?: number;
}) {
  const t = useT();
  const [form, setForm] = useState<AddForm>(() => blankForm(loadLastMode()));
  const [items, setItems] = useState<Item[]>([]);
  const [preview, setPreview] = useState<{ key: string; row: PreviewRow | null } | null>(null);
  const [previewErrors, setPreviewErrors] = useState<EntryError[]>([]);
  const [previewing, setPreviewing] = useState(false);
  const [previewFailure, setPreviewFailure] = useState<string | null>(null);
  const [bookErrors, setBookErrors] = useState<EntryError[]>([]);
  const [attempted, setAttempted] = useState(false);
  const [saving, setSaving] = useState(false);
  const [quote, setQuote] = useState<{ symbol: string; price: PV; ts: string } | null>(null);
  const seq = useRef(0);

  const spec = MODE_SPEC[form.mode];
  const currency = form.symbol?.currency ?? null;
  const localErrors = useMemo(() => (form.symbol ? validateRows([formDraft(form)]) : []), [form]);
  const position = form.symbol && localErrors.length === 0 ? formPosition(form) : null;
  const positionKey = position ? JSON.stringify(position) : "";

  // last price of the picked symbol (step 1); the preview shows it again with its as-of
  const quoteFn = api.quote;
  const picked = form.symbol?.canonical ?? null;
  useEffect(() => {
    setQuote(null);
    if (!picked || !quoteFn) return;
    let alive = true;
    quoteFn(picked)
      .then((q) => {
        const value = Number(q.quote.price);
        if (alive && Number.isFinite(value)) setQuote({ symbol: picked, price: { value, provenance_id: q.provenance_id ?? "" }, ts: q.quote.ts });
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [picked, quoteFn]);

  // live preview: debounced, and an older answer never overwrites a newer one
  const previewFn = api.preview;
  useEffect(() => {
    const mine = ++seq.current;
    if (!positionKey) {
      setPreview(null);
      setPreviewErrors([]);
      setPreviewFailure(null);
      setPreviewing(false);
      return;
    }
    const body = JSON.parse(positionKey) as PositionInput;
    const t = window.setTimeout(() => {
      setPreviewing(true);
      previewFn(portfolioId, [body])
        .then((res) => {
          if (mine !== seq.current) return;
          setPreview({ key: positionKey, row: res.rows[0] ?? null });
          setPreviewErrors(res.errors);
          setPreviewFailure(null);
        })
        .catch((err: unknown) => {
          if (mine !== seq.current) return;
          setPreview({ key: positionKey, row: null });
          const rows = entryErrors(err);
          if (rows) {
            setPreviewErrors(rows);
            setPreviewFailure(null);
          } else {
            setPreviewErrors([]);
            setPreviewFailure(err instanceof Error ? err.message : String(err));
          }
        })
        .finally(() => {
          if (mine === seq.current) setPreviewing(false);
        });
    }, previewDelayMs);
    return () => window.clearTimeout(t);
  }, [positionKey, portfolioId, previewFn, previewDelayMs]);

  const update = (patch: Partial<AddForm>) => setForm((f) => ({ ...f, ...patch }));
  const setMode = (mode: GuidedMode) => {
    rememberMode(mode);
    // keep the first number when it means the same thing (quantity ↔ quantity)
    setForm((f) => ({ ...f, mode, first: MODE_SPEC[f.mode].first.field === MODE_SPEC[mode].first.field ? f.first : "", second: "" }));
  };
  /** a typed "-" / "+" flips the gain/loss toggle instead of staying in the text */
  const setSecond = (v: string) => {
    if (spec.signed && /^\s*[-+]/.test(v)) update({ second: v.replace(/^\s*[-+]\s*/, ""), loss: /^\s*-/.test(v) });
    else update({ second: v });
  };

  // only the answer for what is in the form right now counts
  const fresh = preview !== null && preview.key === positionKey;
  const current = fresh ? preview.row : null;
  const freshErrors = fresh ? previewErrors : [];

  // errors shown at each input: local checks (after a submit attempt) + the backend's
  const previewRowErrors = current ? current.errors : freshErrors.filter((e) => e.index !== null);
  const previewWarn = current?.status === "ok";
  const shownLocal = attempted ? localErrors : [];
  const at = (field: "symbol" | "first" | "second", list: EntryError[]) =>
    list.filter((e) => {
      if (field === "symbol") return e.field === "symbol";
      const draftField = e.field ? API_FIELD[e.field] : undefined;
      return draftField === (field === "first" ? spec.first.field : spec.second.field);
    });
  const general = previewRowErrors.filter((e) => at("symbol", [e]).length + at("first", [e]).length + at("second", [e]).length === 0);
  const bookLevel = [...freshErrors.filter((e) => e.index === null), ...bookErrors];

  const blocked = current?.status === "error" || (!current && freshErrors.some((e) => e.index !== null));
  const invalid = (slot: "first" | "second") =>
    at(slot, shownLocal).length > 0 || (!previewWarn && at(slot, previewRowErrors).length > 0);
  const filled = form.symbol !== null || form.first.trim() !== "" || form.second.trim() !== "";

  /** the current form as a list item, or null (errors shown) when it cannot be added */
  const takeForm = (): Item | null => {
    setAttempted(true);
    if (!form.symbol || localErrors.length || blocked) return null;
    return { key: `${form.symbol.canonical}-${Date.now().toString(36)}-${items.length}`, form, position: formPosition(form), preview: current, errors: [] };
  };
  const resetForm = () => {
    setForm(blankForm(form.mode));
    setAttempted(false);
  };

  const addAnother = () => {
    const item = takeForm();
    if (!item) return;
    setItems((prev) => [...prev, item]);
    setBookErrors([]);
    resetForm();
  };

  const editItem = (key: string) => {
    const item = items.find((i) => i.key === key);
    if (!item || filled) return;
    setItems((prev) => prev.filter((i) => i.key !== key));
    setForm(item.form);
    setAttempted(item.errors.length > 0);
  };

  const saveAll = async () => {
    let list = items;
    if (filled) {
      const item = takeForm();
      if (!item) return;
      list = [...items, item];
      setItems(list);
      resetForm();
    }
    if (!list.length) return;
    setSaving(true);
    try {
      const result = await onSave(list.map((i) => i.position));
      if (!result) {
        onClose();
        return;
      }
      const grouped = groupErrors(result);
      setItems(list.map((it, i) => ({ ...it, errors: grouped.rows.get(i) ?? [] })));
      setBookErrors(grouped.book);
    } catch (err) {
      setBookErrors([{ index: null, symbol: null, field: null, message: err instanceof Error ? err.message : String(err) }]);
    } finally {
      setSaving(false);
    }
  };

  const fields = (["first", "second"] as const).map((slot) => {
    const field = slot === "first" ? spec.first.field : spec.second.field;
    const required = slot === "first" || spec.second.required;
    return { slot, field, label: fieldLabel(field, currency), required };
  });

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("เพิ่ม position", "Add position")}
      className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4"
    >
      <div data-testid="add-position-dialog" className="flex max-h-[90vh] w-full max-w-xl flex-col rounded-lg border border-line-strong bg-layer-1 shadow-xl">
        <div className="flex items-center border-b border-line px-4 py-2">
          <h2 className="text-[14px] font-semibold">{t("เพิ่ม position", "Add position")}</h2>
          <span className="flex-1" />
          <button type="button" aria-label={t("ปิด", "Close")} onClick={onClose} className="rounded-md p-1 text-muted hover:bg-layer-2">
            <X className="size-4" aria-hidden />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 text-[12.5px]">
          <section className="space-y-1.5">
            <h3 className="text-[12px] font-medium text-muted">1 · {t("เลือกหุ้น", "Pick a stock")}</h3>
            <SymbolSearch
              value={form.symbol}
              search={search}
              autoFocus
              onPick={(o) => {
                update({ symbol: o.symbol });
                setAttempted(false);
              }}
            />
            {form.symbol ? (
              <div data-testid="add-symbol" className="flex flex-wrap items-center gap-1.5 text-[12px]">
                <span className="num font-medium">{form.symbol.canonical}</span>
                <AssetBadge symbol={form.symbol} />
                {MARKET_LABEL[form.symbol.market] ? (
                  <span className="rounded-sm bg-layer-2 px-1.5 py-0.5 text-[11px] text-muted">{MARKET_LABEL[form.symbol.market]}</span>
                ) : null}
                <span className="num rounded-sm bg-layer-2 px-1.5 py-0.5 text-[11px] text-muted">{form.symbol.currency}</span>
                {quote && quote.symbol === form.symbol.canonical ? (
                  <span data-testid="add-quote" className="ml-auto text-muted">
                    {t("ราคาล่าสุด", "Last price")} <ProvValue pv={quote.price} /> <span className="num text-[11px] text-subtle">{quote.ts.slice(0, 16).replace("T", " ")}</span>
                  </span>
                ) : null}
              </div>
            ) : null}
            <FieldErrors errors={at("symbol", [...shownLocal, ...previewRowErrors])} testId="error-symbol" />
          </section>

          {form.symbol ? (
            <section className="space-y-2">
              <div className="flex items-baseline gap-2">
                <h3 className="text-[12px] font-medium text-muted">2 · {t("คุณเห็นอะไรในแอปโบรกเกอร์?", "What does your broker app show?")}</h3>
                <span className="flex-1" />
                {onPercentMode ? (
                  <button type="button" data-testid="add-percent-mode" onClick={onPercentMode} className="text-[11.5px] text-accent hover:underline">
                    {t("กรอกแค่ % น้ำหนัก", "Enter % weight only")}
                  </button>
                ) : null}
              </div>
              <div role="radiogroup" aria-label={t("โหมดกรอก", "Entry mode")} className="grid grid-cols-1 gap-1 rounded-md bg-layer-2 p-1 sm:grid-cols-2">
                {ENTRY_MODES.map((m) => (
                  <button
                    key={m}
                    type="button"
                    role="radio"
                    aria-checked={form.mode === m}
                    data-testid={`mode-${m}`}
                    onClick={() => setMode(m)}
                    className={clsx(
                      "rounded-sm px-2 py-1 text-left text-[12px]",
                      form.mode === m ? "bg-layer-1 text-fg shadow-sm ring-1 ring-accent" : "text-muted hover:text-fg",
                    )}
                  >
                    {modeLabel(m, currency)}
                  </button>
                ))}
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                {fields.map(({ slot, field, label, required }) => (
                  <div key={slot}>
                    <label className="block">
                      <span className="mb-0.5 block text-[11.5px] text-muted">
                        {label}
                        {required ? "" : t(" (ไม่บังคับ)", " (optional)")}
                      </span>
                      <input
                        aria-label={label}
                        data-testid={`add-${slot}`}
                        inputMode="decimal"
                        value={slot === "first" ? form.first : form.second}
                        placeholder={field === "pnlPct" ? t("เช่น 14.52", "e.g. 14.52") : field === "quantity" ? t("เช่น 100", "e.g. 100") : `0.00 ${moneyUnit(currency)}`}
                        onChange={(e) => (slot === "first" ? update({ first: e.target.value }) : setSecond(e.target.value))}
                        className={clsx(inputCls, invalid(slot) && "border-error")}
                      />
                    </label>
                    {slot === "second" && spec.signed ? (
                      <div role="group" aria-label={t("กำไรหรือขาดทุน", "Gain or loss")} className="mt-1 flex gap-1">
                        <button
                          type="button"
                          data-testid="sign-gain"
                          aria-pressed={!form.loss}
                          onClick={() => update({ loss: false })}
                          className={clsx("flex-1 rounded-sm px-2 py-0.5 text-[12px]", !form.loss ? "bg-up/15 font-medium text-up" : "bg-layer-2 text-muted")}
                        >
                          {t("กำไร", "Gain")}
                        </button>
                        <button
                          type="button"
                          data-testid="sign-loss"
                          aria-pressed={form.loss}
                          onClick={() => update({ loss: true })}
                          className={clsx("flex-1 rounded-sm px-2 py-0.5 text-[12px]", form.loss ? "bg-down/15 font-medium text-down" : "bg-layer-2 text-muted")}
                        >
                          {t("ขาดทุน", "Loss")}
                        </button>
                      </div>
                    ) : null}
                    <FieldErrors errors={at(slot, shownLocal)} testId={`local-error-${slot}`} />
                    <FieldErrors errors={at(slot, previewRowErrors)} warn={previewWarn} testId={`error-${slot}`} />
                  </div>
                ))}
              </div>

              <div data-testid="add-preview" className="rounded-md border border-line bg-bg/40 p-3">
                {current && current.status === "ok" ? (
                  <PreviewSummary row={current} />
                ) : previewing ? (
                  <p className="text-subtle">{t("กำลังคำนวณจากราคาล่าสุด…", "Computing from the latest price…")}</p>
                ) : previewFailure ? (
                  <p className="text-error">{t("preview ไม่ได้", "Preview failed")}: {previewFailure}</p>
                ) : current?.status === "error" ? (
                  <p className="text-error">{t("ข้อมูลแถวนี้ใช้ไม่ได้ — ดูข้อความที่ช่องด้านบน", "This row is not valid — see the messages on the fields above")}</p>
                ) : (
                  <p className="text-subtle">{t("กรอกตัวเลขแล้วระบบจะคำนวณส่วนที่เหลือให้", "Enter the numbers and the rest is computed for you")}</p>
                )}
                <FieldErrors errors={general} warn={previewWarn} testId="error-row" />
                <p className="mt-2 text-[11px] text-subtle">{t("ระบบคำนวณจากราคาล่าสุด ค่าจะขยับตามตลาด", "Computed from the latest price; values move with the market")}</p>
              </div>
            </section>
          ) : null}

          {items.length ? (
            <section data-testid="add-items" className="space-y-1">
              <h3 className="text-[12px] font-medium text-muted">{t("รายการที่จะบันทึก", "To be saved")} ({items.length})</h3>
              <ul className="divide-y divide-line/60 rounded-md border border-line">
                {items.map((it) => (
                  <li key={it.key} data-testid="add-item" data-symbol={it.position.symbol} className={clsx("px-2 py-1.5", it.errors.length && "bg-error/5")}>
                    <div className="flex items-center gap-2">
                      <span className="num font-medium">{it.form.symbol?.display ?? it.position.symbol}</span>
                      <span className="text-[11px] text-subtle">{modeLabel(it.form.mode, it.form.symbol?.currency)}</span>
                      <span className="flex-1" />
                      <button
                        type="button"
                        aria-label={t(`แก้ ${it.position.symbol}`, `Edit ${it.position.symbol}`)}
                        disabled={filled}
                        title={filled ? t("เพิ่มหรือล้างตัวที่กรอกอยู่ก่อน", "Add or clear the row being entered first") : t("แก้", "Edit")}
                        onClick={() => editItem(it.key)}
                        className="rounded-sm p-1 text-subtle hover:bg-layer-2 hover:text-fg disabled:opacity-40"
                      >
                        <Pencil className="size-3.5" aria-hidden />
                      </button>
                      <button
                        type="button"
                        aria-label={t(`ลบ ${it.position.symbol}`, `Remove ${it.position.symbol}`)}
                        onClick={() => setItems((prev) => prev.filter((i) => i.key !== it.key))}
                        className="rounded-sm p-1 text-subtle hover:bg-error/15 hover:text-error"
                      >
                        <Trash2 className="size-3.5" aria-hidden />
                      </button>
                    </div>
                    {it.preview && it.preview.status === "ok" ? <PreviewSummary row={it.preview} compact /> : null}
                    {it.errors.length ? (
                      <p data-testid="item-error" className="text-[11.5px] text-error">
                        {it.errors.map((e) => e.message).join(" · ")}
                      </p>
                    ) : null}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {bookLevel.length ? (
            <ul data-testid="add-book-errors" className="space-y-0.5 rounded-md bg-error/10 p-2 text-error">
              {bookLevel.map((e, i) => (
                <li key={i}>{e.message}</li>
              ))}
            </ul>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line px-4 py-2">
          <button type="button" onClick={onClose} className="rounded-md px-3 py-1 text-[13px] text-muted hover:bg-layer-2">
            {t("ยกเลิก", "Cancel")}
          </button>
          <button
            type="button"
            data-testid="add-another"
            disabled={!form.symbol || saving}
            onClick={addAnother}
            className="inline-flex items-center gap-1 rounded-md bg-layer-2 px-3 py-1 text-[13px] disabled:opacity-50"
          >
            <Plus className="size-3.5" aria-hidden /> {t("เพิ่มอีกตัว", "Add another")}
          </button>
          <button
            type="button"
            data-testid="add-save"
            disabled={saving || (!items.length && !form.symbol)}
            onClick={() => void saveAll()}
            className="rounded-md bg-accent px-3 py-1 text-[13px] text-bg disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving
              ? t("กำลังบันทึก…", "Saving…")
              : `${t("บันทึกทั้งหมด", "Save all")}${items.length + (filled ? 1 : 0) > 1 ? ` (${items.length + (filled ? 1 : 0)})` : ""}`}
          </button>
        </div>
      </div>
    </div>
  );
}
