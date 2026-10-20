"use client";

/**
 * Paste-from-broker dialog (phase-16 §16.3, ui-spec §6): paste the holdings screen / email
 * summary of a broker app → `POST /portfolios/parse-holdings` (LLM, no prices, no side effect)
 * → editable rows (symbol · entry mode · the two numbers; unreadable lines listed as a
 * warning) → live preview of what the backend computes (`…/positions/preview`) → save,
 * appended to the book (default) or replacing it. Numbers are only formatted here (F1).
 */

import { clsx } from "clsx";
import { ClipboardPaste, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  ENTRY_MODES,
  MODE_SPEC,
  buildPositions,
  draftFromPosition,
  fieldLabel,
  groupErrors,
  isGuidedMode,
  modeLabel,
  validateRows,
  type DraftField,
  type DraftRow,
} from "@/lib/holdings";
import { tr, useT } from "@/lib/i18n";
import {
  entryErrors,
  isLlmNotConfigured,
  portfolioApi,
  type EntryError,
  type MarketHint,
  type ParsedHoldings,
  type ParseHoldingsRequest,
  type PositionInput,
  type PositionsPreview,
} from "@/lib/portfolio-api";

import { PreviewSummary } from "./AddPositionDialog";

export interface PasteApi {
  parse: (body: ParseHoldingsRequest) => Promise<ParsedHoldings>;
  preview: (id: string | null, positions: PositionInput[]) => Promise<PositionsPreview>;
}

const defaultApi: PasteApi = { parse: portfolioApi.parseHoldings, preview: portfolioApi.positionsPreview };

export const PASTE_MAX_CHARS = 20_000;

/** `label` = [Thai, English] */
const HINTS: { id: MarketHint | "auto"; label: readonly [string, string] }[] = [
  { id: "TH", label: ["ไทย", "Thai"] },
  { id: "US", label: ["สหรัฐฯ", "US"] },
  { id: "auto", label: ["ให้ AI เดา", "Let the AI guess"] },
];

const cellCls =
  "num w-full min-w-20 rounded-sm border border-line bg-bg px-1.5 py-0.5 text-right text-[12.5px] outline-none focus:border-accent";

const patchOf = (field: DraftField, value: string): Partial<DraftRow> => {
  const patch: Partial<DraftRow> = {};
  patch[field] = value;
  return patch;
};

export function PasteHoldingsDialog({
  portfolioId,
  onClose,
  onSave,
  hasPositions = false,
  api = defaultApi,
  previewDelayMs = 400,
}: {
  /** null → the default book's `/portfolio/positions/preview` */
  portfolioId: string | null;
  onClose: () => void;
  /** PUT the rows (`replace` = instead of the book's positions); resolves to errors indexed by these rows (422) or null */
  onSave: (rows: PositionInput[], replace: boolean) => Promise<EntryError[] | null>;
  /** the book already has positions (shows the append / replace choice) */
  hasPositions?: boolean;
  api?: PasteApi;
  previewDelayMs?: number;
}) {
  const t = useT();
  const [text, setText] = useState("");
  const [hint, setHint] = useState<MarketHint | "auto">("auto");
  const [parsing, setParsing] = useState(false);
  const [parsed, setParsed] = useState<Omit<ParsedHoldings, "rows"> | null>(null);
  const [rows, setRows] = useState<DraftRow[] | null>(null);
  const [llmMissing, setLlmMissing] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ key: string; result: PositionsPreview | null; errors: EntryError[] } | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [saveErrors, setSaveErrors] = useState<EntryError[]>([]);
  const [attempted, setAttempted] = useState(false);
  const [replace, setReplace] = useState(false);
  const [saving, setSaving] = useState(false);
  /** currency of each typed symbol, from the last preview that resolved it (labels stay put while a new preview runs) */
  const [currencies, setCurrencies] = useState<Record<string, string>>({});
  const seq = useRef(0);

  const parse = async () => {
    if (!text.trim()) return;
    setParsing(true);
    setFailure(null);
    setLlmMissing(false);
    setSaveErrors([]);
    setAttempted(false);
    try {
      const out = await api.parse({ text, market_hint: hint === "auto" ? null : hint });
      setParsed({ unparsed: out.unparsed, mode: out.mode, provider: out.provider, model: out.model });
      setRows(out.rows.map(draftFromPosition));
    } catch (err) {
      if (isLlmNotConfigured(err)) setLlmMissing(true);
      else setFailure(err instanceof Error ? err.message : String(err));
    } finally {
      setParsing(false);
    }
  };

  const positions = useMemo(() => (rows ? buildPositions(rows) : []), [rows]);
  const positionsKey = positions.length ? JSON.stringify(positions) : "";

  // live preview of every row (debounced, stale answers dropped)
  const previewFn = api.preview;
  useEffect(() => {
    const mine = ++seq.current;
    if (!positionsKey) {
      setPreview(null);
      setPreviewing(false);
      return;
    }
    const body = JSON.parse(positionsKey) as PositionInput[];
    const t = window.setTimeout(() => {
      setPreviewing(true);
      previewFn(portfolioId, body)
        .then((result) => {
          if (mine !== seq.current) return;
          setPreview({ key: positionsKey, result, errors: result.errors });
          const learned: Record<string, string> = {};
          for (const r of result.rows) {
            const typed = body[r.index]?.symbol.trim().toUpperCase();
            if (typed && r.currency) learned[typed] = r.currency;
          }
          setCurrencies((prev) => ({ ...prev, ...learned }));
        })
        .catch((err: unknown) => {
          if (mine !== seq.current) return;
          const rowsErr = entryErrors(err);
          setPreview({
            key: positionsKey,
            result: null,
            errors: rowsErr ?? [{ index: null, symbol: null, field: null, message: `${tr("preview ไม่ได้", "Preview failed")}: ${err instanceof Error ? err.message : String(err)}` }],
          });
        })
        .finally(() => {
          if (mine === seq.current) setPreviewing(false);
        });
    }, previewDelayMs);
    return () => window.clearTimeout(t);
  }, [positionsKey, portfolioId, previewFn, previewDelayMs]);

  const fresh = preview && preview.key === positionsKey ? preview : null;
  const previewRows = useMemo(() => new Map((fresh?.result?.rows ?? []).map((r) => [r.index, r])), [fresh]);
  const localErrors = useMemo(() => (rows ? validateRows(rows) : []), [rows]);
  const grouped = useMemo(
    () => groupErrors([...(attempted ? localErrors : []), ...(fresh?.result ? [] : (fresh?.errors ?? [])), ...saveErrors]),
    [attempted, localErrors, fresh, saveErrors],
  );
  const anyRowError = [...previewRows.values()].some((r) => r.status === "error");
  const bookErrors = [
    ...grouped.book,
    ...(fresh?.result ? fresh.result.errors.filter((e) => e.index === null) : []),
    ...(attempted && anyRowError ? [{ index: null, symbol: null, field: null, message: t("แก้หรือลบแถวที่มีปัญหาก่อนบันทึก", "Fix or remove the rows with problems before saving") }] : []),
  ];

  const edit = (key: string, patch: Partial<DraftRow>) => {
    setRows((prev) => (prev ?? []).map((r) => (r.key === key ? { ...r, ...patch } : r)));
    setSaveErrors([]);
  };
  const remove = (key: string) => {
    setRows((prev) => (prev ?? []).filter((r) => r.key !== key));
    setSaveErrors([]);
  };

  const save = async () => {
    if (!rows?.length) return;
    setAttempted(true);
    if (localErrors.length || anyRowError) return;
    // the preview resolved each symbol (e.g. "PTT" → "PTT.BK"): save the canonical one
    const body = positions.map((p, i) => {
      const row = previewRows.get(i);
      return row && row.status === "ok" && row.symbol.canonical ? { ...p, symbol: row.symbol.canonical } : p;
    });
    setSaving(true);
    try {
      const result = await onSave(body, replace);
      if (!result) {
        onClose();
        return;
      }
      setSaveErrors(result);
    } catch (err) {
      setSaveErrors([{ index: null, symbol: null, field: null, message: err instanceof Error ? err.message : String(err) }]);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div role="dialog" aria-modal="true" aria-label={t("วางข้อความจากแอปโบรกเกอร์", "Paste from your broker app")} className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4">
      <div data-testid="paste-dialog" className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-lg border border-line-strong bg-layer-1 shadow-xl">
        <div className="flex items-center border-b border-line px-4 py-2">
          <h2 className="text-[14px] font-semibold">{t("วางข้อความจากแอปโบรกเกอร์", "Paste from your broker app")}</h2>
          <span className="flex-1" />
          <button type="button" aria-label={t("ปิด", "Close")} onClick={onClose} className="rounded-md p-1 text-muted hover:bg-layer-2">
            <X className="size-4" aria-hidden />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 text-[12.5px]">
          <div>
            <textarea
              aria-label={t("ข้อความจากแอปโบรกเกอร์", "Text from your broker app")}
              data-testid="paste-text"
              value={text}
              maxLength={PASTE_MAX_CHARS}
              rows={6}
              onChange={(e) => setText(e.target.value)}
              placeholder={t(
                "คัดลอกหน้าพอร์ตจากแอป/อีเมลสรุปพอร์ตมาวางได้เลย เช่น\nNVDA 549.96 USD +14.52%\nPTT 1,000 32.50 35.00 +7.69%",
                "Copy the portfolio screen from your app or a statement email and paste it here, e.g.\nNVDA 549.96 USD +14.52%\nPTT 1,000 32.50 35.00 +7.69%",
              )}
              className="num w-full resize-y rounded-md border border-line bg-bg px-2 py-1.5 text-left text-[12.5px] text-fg outline-none placeholder:text-subtle focus:border-accent"
            />
            <div className="mt-1 flex flex-wrap items-center gap-2">
              <span className="text-[11.5px] text-muted">{t("ตลาด", "Market")}</span>
              <div role="radiogroup" aria-label={t("ตลาด", "Market")} className="flex rounded-md bg-layer-2 p-0.5">
                {HINTS.map((h) => (
                  <button
                    key={h.id}
                    type="button"
                    role="radio"
                    aria-checked={hint === h.id}
                    data-testid={`hint-${h.id}`}
                    onClick={() => setHint(h.id)}
                    className={clsx("rounded-sm px-2 py-0.5 text-[12px]", hint === h.id ? "bg-layer-1 text-fg shadow-sm" : "text-muted")}
                  >
                    {t(...h.label)}
                  </button>
                ))}
              </div>
              <span className="num text-[11px] text-subtle">
                {text.length.toLocaleString("en-US")}/{PASTE_MAX_CHARS.toLocaleString("en-US")}
              </span>
              <span className="flex-1" />
              <button
                type="button"
                data-testid="paste-parse"
                disabled={!text.trim() || parsing}
                onClick={() => void parse()}
                className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1 text-[12.5px] text-bg disabled:opacity-50"
              >
                <ClipboardPaste className="size-3.5" aria-hidden /> {parsing ? t("AI กำลังอ่าน…", "AI is reading…") : t("ให้ AI อ่าน", "Let the AI read it")}
              </button>
            </div>
            <p className="mt-1 text-[11px] text-subtle">
              {t(
                "AI อ่านเฉพาะตัวเลขที่เห็นในข้อความ ไม่เดาตัวเลข · ราคาและกำไร/ขาดทุนคำนวณใหม่จากราคาล่าสุด",
                "The AI only reads numbers that appear in the text and never guesses · prices and P/L are recomputed from the latest price",
              )}
            </p>
          </div>

          {llmMissing ? (
            <p data-testid="paste-llm-missing" className="rounded-md bg-warning/10 px-3 py-2 text-warning">
              {t("ยังไม่มี LLM ที่ใช้ได้ — ตั้งค่า LLM ใน Settings ก่อน", "No usable LLM yet — set one up in Settings first")}{" "}
              <Link href="/settings" className="text-accent underline">
                {t("ไปที่ Settings", "Go to Settings")}
              </Link>
            </p>
          ) : null}
          {failure ? <p className="rounded-md bg-error/10 px-3 py-2 text-error">{t("อ่านข้อความไม่ได้", "Could not read the text")}: {failure}</p> : null}

          {parsed?.mode === "failed" ? (
            <p data-testid="paste-failed" className="rounded-md bg-error/10 px-3 py-2 text-error">
              {t(
                "AI อ่านข้อความนี้ไม่ได้ — ลองตัดให้เหลือเฉพาะรายการหุ้น หรือกรอกด้วย “เพิ่ม position”",
                "The AI could not read this text — trim it down to the holdings list, or enter them with “Add position”",
              )}
            </p>
          ) : parsed?.mode === "fallback_parsed" ? (
            <p data-testid="paste-fallback" className="rounded-md bg-layer-2 px-3 py-1.5 text-[11.5px] text-muted">
              {t(
                "AI ตอบไม่ตรงรูปแบบ ระบบแปลงคำตอบให้แทน — ตรวจตัวเลขอีกครั้งก่อนบันทึก",
                "The AI answered in the wrong format, so the system converted it — double-check the numbers before saving",
              )}
            </p>
          ) : null}
          {parsed?.unparsed.length ? (
            <div data-testid="paste-unparsed" className="rounded-md bg-warning/10 px-3 py-1.5 text-warning">
              <p>
                {t(
                  `บรรทัดที่ AI อ่านไม่ออก (${parsed.unparsed.length}) — ไม่ได้ถูกเพิ่ม:`,
                  `Lines the AI could not read (${parsed.unparsed.length}) — not added:`,
                )}
              </p>
              <ul className="num list-inside list-disc text-[11.5px]">
                {parsed.unparsed.map((l, i) => (
                  <li key={i}>{l}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {rows ? (
            rows.length ? (
              <div>
                <div className="mb-1 flex items-center gap-2 text-[11.5px] text-muted">
                  <span>{t(`แถวที่อ่านได้ (${rows.length}) — แก้ได้ก่อนบันทึก`, `Rows read (${rows.length}) — edit before saving`)}</span>
                  {parsed?.provider ? (
                    <span className="num text-subtle">
                      {t("อ่านโดย", "read by")} {parsed.provider}
                      {parsed.model ? ` · ${parsed.model}` : ""}
                    </span>
                  ) : null}
                  <span className="flex-1" />
                  {previewing ? <span className="text-subtle">{t("กำลังคำนวณ…", "Computing…")}</span> : null}
                </div>
                <div className="overflow-x-auto rounded-md border border-line">
                  <table data-testid="paste-rows" className="w-full text-[12.5px]">
                    <thead className="bg-layer-2 text-left text-[11.5px] text-subtle">
                      <tr>
                        <th className="px-2 py-1 font-normal">Symbol</th>
                        <th className="px-2 py-1 font-normal">{t("สิ่งที่เห็นในแอป", "What the app shows")}</th>
                        <th className="px-2 py-1 text-right font-normal">{t("ตัวเลข 1", "Number 1")}</th>
                        <th className="px-2 py-1 text-right font-normal">{t("ตัวเลข 2", "Number 2")}</th>
                        <th className="px-2 py-1 font-normal">{t("ระบบคำนวณ", "Computed")}</th>
                        <th className="px-2 py-1" />
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((r, i) => {
                        const pr = previewRows.get(i);
                        const currency = pr?.currency || currencies[r.symbol.trim().toUpperCase()] || null;
                        const errs = [...(grouped.rows.get(i) ?? []), ...(pr?.errors ?? [])];
                        const spec = r.kind === "weight" ? null : MODE_SPEC[r.kind];
                        const label = r.symbol || `${t("แถว", "Row")} ${i + 1}`;
                        return (
                          <tr key={r.key} data-testid="paste-row" className={clsx("border-t border-line/60 align-top", pr?.status === "error" && "bg-error/5")}>
                            <td className="px-2 py-1">
                              <input
                                aria-label={`symbol ${i + 1}`}
                                value={r.symbol}
                                onChange={(e) => edit(r.key, { symbol: e.target.value })}
                                className={clsx(cellCls, "min-w-24 text-left")}
                              />
                            </td>
                            <td className="px-2 py-1">
                              <select
                                aria-label={`${t("โหมดกรอก", "Entry mode")} ${label}`}
                                value={r.kind}
                                onChange={(e) => {
                                  const kind = e.target.value;
                                  if (isGuidedMode(kind) || kind === "weight") edit(r.key, { kind });
                                }}
                                className="max-w-44 rounded-sm border border-line bg-bg px-1 py-0.5 text-[12px] outline-none focus:border-accent"
                              >
                                {ENTRY_MODES.map((m) => (
                                  <option key={m} value={m}>
                                    {modeLabel(m, currency)}
                                  </option>
                                ))}
                                {r.kind === "weight" ? <option value="weight">{modeLabel("weight")}</option> : null}
                              </select>
                            </td>
                            {spec ? (
                              ([spec.first.field, spec.second.field] as DraftField[]).map((field) => (
                                <td key={field} className="px-2 py-1">
                                  <input
                                    aria-label={`${fieldLabel(field, currency)} ${label}`}
                                    title={fieldLabel(field, currency)}
                                    placeholder={fieldLabel(field, currency)}
                                    inputMode="decimal"
                                    value={r[field]}
                                    onChange={(e) => edit(r.key, patchOf(field, e.target.value))}
                                    className={cellCls}
                                  />
                                </td>
                              ))
                            ) : (
                              <td colSpan={2} className="px-2 py-1">
                                <input
                                  aria-label={`${t("% น้ำหนัก", "% weight")} ${label}`}
                                  inputMode="decimal"
                                  value={r.weightPct}
                                  onChange={(e) => edit(r.key, { weightPct: e.target.value })}
                                  className={cellCls}
                                />
                              </td>
                            )}
                            <td className="px-2 py-1">
                              {pr && pr.status === "ok" ? <PreviewSummary row={pr} compact /> : null}
                              {errs.length ? (
                                <p data-testid="paste-row-error" className={clsx("text-[11.5px]", pr?.status === "ok" && !grouped.rows.get(i)?.length ? "text-warning" : "text-error")}>
                                  {errs.map((e) => e.message).join(" · ")}
                                </p>
                              ) : null}
                            </td>
                            <td className="px-1 py-1 text-right">
                              <button
                                type="button"
                                aria-label={`${t("ลบ", "Remove")} ${label}`}
                                onClick={() => remove(r.key)}
                                className="rounded-sm p-1 text-subtle hover:bg-error/15 hover:text-error"
                              >
                                <Trash2 className="size-3.5" aria-hidden />
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <p className="mt-1 text-[11px] text-subtle">
                  {t(
                    "กำไร/ขาดทุนที่เป็นลบ = ขาดทุน · ระบบคำนวณจากราคาล่าสุด ค่าจะขยับตามตลาด",
                    "A negative P/L = loss · computed from the latest price; values move with the market",
                  )}
                </p>
              </div>
            ) : parsed?.mode !== "failed" ? (
              <p className="text-muted">{t("ไม่พบรายการหุ้นในข้อความนี้", "No holdings found in this text")}</p>
            ) : null
          ) : null}

          {bookErrors.length ? (
            <ul data-testid="paste-book-errors" className="space-y-0.5 rounded-md bg-error/10 p-2 text-error">
              {bookErrors.map((e, i) => (
                <li key={i}>{e.message}</li>
              ))}
            </ul>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-line px-4 py-2">
          {hasPositions && rows?.length ? (
            <div role="radiogroup" aria-label={t("วิธีบันทึก", "How to save")} className="flex rounded-md bg-layer-2 p-0.5 text-[12px]">
              <button
                type="button"
                role="radio"
                aria-checked={!replace}
                data-testid="save-append"
                onClick={() => setReplace(false)}
                className={clsx("rounded-sm px-2 py-0.5", !replace ? "bg-layer-1 text-fg shadow-sm" : "text-muted")}
              >
                {t("เพิ่มต่อท้าย", "Append")}
              </button>
              <button
                type="button"
                role="radio"
                aria-checked={replace}
                data-testid="save-replace"
                onClick={() => setReplace(true)}
                className={clsx("rounded-sm px-2 py-0.5", replace ? "bg-layer-1 text-error shadow-sm" : "text-muted")}
              >
                {t("แทนที่ทั้งพอร์ต", "Replace the whole portfolio")}
              </button>
            </div>
          ) : null}
          <span className="flex-1" />
          <button type="button" onClick={onClose} className="rounded-md px-3 py-1 text-[13px] text-muted hover:bg-layer-2">
            {t("ยกเลิก", "Cancel")}
          </button>
          <button
            type="button"
            data-testid="paste-save"
            disabled={saving || !rows?.length}
            onClick={() => void save()}
            className="rounded-md bg-accent px-3 py-1 text-[13px] text-bg disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? t("กำลังบันทึก…", "Saving…") : t("บันทึก", "Save")}
          </button>
        </div>
      </div>
    </div>
  );
}
