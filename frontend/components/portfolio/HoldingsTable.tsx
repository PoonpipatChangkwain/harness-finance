"use client";

/**
 * Holdings table with inline editing (phase-13 §13.2, ui-spec §6).
 * - qty / avg cost / target % / note are edited in place; "+ เพิ่ม" opens the guided
 *   AddPositionDialog (phase-16: 6 entry modes + live preview) whose rows are merged into
 *   the book and PUT; draft rows still on the table (e.g. after leaving %-only mode) get a
 *   small menu of the 6 entry modes
 * - a 422 comes back per row and is shown on that row
 * - "% อย่างเดียว" mode hides qty/cost/P&L and shows the live total of the declared %
 * - Price / Value / Weight / P&L come from the analytics only (F1), with provenance
 * - sort, filter and column visibility via TanStack Table
 */

import {
  columnFilteringFeature,
  columnVisibilityFeature,
  createColumnHelper,
  createFilteredRowModel,
  createSortedRowModel,
  filterFn_includesString,
  flexRender,
  globalFilteringFeature,
  rowSortingFeature,
  sortFn_alphanumeric,
  sortFn_basic,
  tableFeatures,
  useTable,
  type SortingState,
} from "@tanstack/react-table";
import { clsx } from "clsx";
import { ClipboardPaste, Columns3, FileUp, Percent, Plus, RotateCcw, Save, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { AssetBadge, SymbolSearch } from "@/components/trade/SymbolSearch";
import type { SymbolInfo } from "@/lib/api";
import {
  ENTRY_MODES,
  MODE_SPEC,
  buildPositions,
  convertRows,
  declaredTotalPct,
  emptyRow,
  fieldLabel,
  groupErrors,
  isGuidedMode,
  isPercentBook,
  mergePositions,
  modeLabel,
  remapErrors,
  rowsFromBook,
  validateRows,
  type DraftField,
  type DraftRow,
  type EntryMode,
} from "@/lib/holdings";
import { localized, tr, useT } from "@/lib/i18n";
import type { EntryError, HoldingAnalytics, PortfolioAnalytics, PortfolioData, PositionInput } from "@/lib/portfolio-api";
import { formatQty, numberText } from "@/lib/portfolio-format";

import { AddPositionDialog, type AddPositionApi } from "./AddPositionDialog";
import { ProvValue } from "./ProvValue";

type SymbolSearchFn = Parameters<typeof SymbolSearch>[0]["search"];

interface Row {
  draft: DraftRow;
  index: number;
  holding: HoldingAnalytics | undefined;
}

const features = tableFeatures({
  rowSortingFeature,
  columnFilteringFeature,
  globalFilteringFeature,
  columnVisibilityFeature,
  filteredRowModel: createFilteredRowModel(),
  sortedRowModel: createSortedRowModel(),
  filterFns: { includesString: filterFn_includesString },
  sortFns: { basic: sortFn_basic, alphanumeric: sortFn_alphanumeric },
});
const col = createColumnHelper<typeof features, Row>();

const COLUMN_LABELS: Record<string, string> = localized({
  symbol: ["Symbol", "Symbol"],
  qty: ["Qty", "Qty"],
  avg: ["Avg cost", "Avg cost"],
  declared: ["% ที่กรอก", "Declared %"],
  price: ["Price", "Price"],
  value: ["Value", "Value"],
  weight: ["Weight%", "Weight%"],
  target: ["Target%", "Target%"],
  pnl: ["P&L", "P&L"],
  pnlPct: ["P&L%", "P&L%"],
  sector: ["Sector", "Sector"],
  note: ["Note", "Note"],
});
const PERCENT_HIDDEN = ["qty", "avg", "price", "value", "pnl", "pnlPct"];
const QTY_HIDDEN = ["declared"];

const inputCls =
  "num w-full min-w-16 rounded-sm border border-transparent bg-transparent px-1 py-0.5 text-right text-[12.5px] outline-none hover:border-line focus:border-accent focus:bg-bg";

function CellInput({
  value,
  onChange,
  label,
  placeholder,
  invalid,
  align = "right",
}: {
  value: string;
  onChange: (v: string) => void;
  label: string;
  placeholder?: string;
  invalid?: boolean;
  align?: "right" | "left";
}) {
  return (
    <input
      aria-label={label}
      value={value}
      placeholder={placeholder}
      inputMode={align === "right" ? "decimal" : undefined}
      onChange={(e) => onChange(e.target.value)}
      className={clsx(inputCls, align === "left" && "text-left font-sans", invalid && "border-error")}
    />
  );
}

const num = (pv: { value: number } | null | undefined) => (pv ? pv.value : undefined);

/** aria-label prefix of each draft input (kept stable for tests / screen readers) */
const FIELD_ARIA: Record<DraftField, string> = {
  quantity: "quantity",
  value: "value",
  avgCost: "avg cost",
  pnlPct: "pnl percent",
  pnlAmount: "pnl amount",
  costTotal: "cost total",
};

const fieldPatch = (field: DraftField, value: string): Partial<DraftRow> => {
  const patch: Partial<DraftRow> = {};
  patch[field] = value;
  return patch;
};

export function HoldingsTable({
  book,
  analytics,
  onSave,
  onImport,
  onPaste,
  startWith,
  search,
  portfolioId = null,
  addApi,
}: {
  book: PortfolioData | null;
  analytics: PortfolioAnalytics | null;
  /** PUT the book; resolves to per-row errors (422) or null when saved */
  onSave: (positions: PositionInput[], cash: string | null) => Promise<EntryError[] | null>;
  onImport: () => void;
  /** "วางข้อความจากแอปโบรกเกอร์" (the parent owns the paste dialog) */
  onPaste?: () => void;
  /** empty-state buttons: open a draft row of this kind (nonce re-triggers) */
  startWith?: { kind: EntryMode; nonce: number } | null;
  search?: SymbolSearchFn;
  /** book id for the add dialog's preview (null = the default book) */
  portfolioId?: string | null;
  /** injectable preview/quote calls of the add dialog (tests) */
  addApi?: AddPositionApi;
}) {
  const t = useT();
  const [rows, setRows] = useState<DraftRow[]>(() => rowsFromBook(book));
  const [percent, setPercent] = useState(() => isPercentBook(book));
  const [cash, setCash] = useState(book?.cash ?? "");
  const [dirty, setDirty] = useState(false);
  const [errors, setErrors] = useState<EntryError[]>([]);
  const [saving, setSaving] = useState(false);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [filter, setFilter] = useState("");
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const [showColumns, setShowColumns] = useState(false);
  const [symbols, setSymbols] = useState<Record<string, SymbolInfo>>({});
  const [adding, setAdding] = useState(false);

  // a saved book (or another book) replaces the drafts unless the user is mid-edit
  // (also right after a save, so the rows follow the book the parent just stored)
  const bookKey = `${book?.id ?? "new"}:${book?.updated_at ?? ""}`;
  useEffect(() => {
    if (dirty) return;
    setRows(rowsFromBook(book));
    setPercent(isPercentBook(book));
    setCash(book?.cash ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only when the saved book changes or edits end
  }, [bookKey, dirty]);

  useEffect(() => {
    if (!startWith) return;
    const pct = startWith.kind === "weight";
    setPercent(pct);
    setRows((prev) => [...convertRows(prev, pct), emptyRow(startWith.kind)]);
    setDirty(true);
  }, [startWith]);

  const edit = (key: string, patch: Partial<DraftRow>) => {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...patch } : r)));
    setDirty(true);
  };
  const remove = (key: string) => {
    setRows((prev) => prev.filter((r) => r.key !== key));
    setDirty(true);
  };
  const add = () => {
    if (!percent) {
      setAdding(true);
      return;
    }
    setRows((prev) => [...prev, emptyRow("weight")]);
    setDirty(true);
  };
  const startPercent = () => {
    setAdding(false);
    setPercent(true);
    setRows((prev) => [...convertRows(prev, true), emptyRow("weight")]);
    setDirty(true);
  };
  const togglePercent = () => {
    const next = !percent;
    setPercent(next);
    setRows((prev) => convertRows(prev, next));
    setDirty(true);
  };
  const discard = () => {
    setRows(rowsFromBook(book));
    setPercent(isPercentBook(book));
    setCash(book?.cash ?? "");
    setErrors([]);
    setDirty(false);
  };
  const check = (): EntryError[] => {
    const found = validateRows(rows);
    if (!percent && cash.trim() && (numberText(cash) === undefined || Number(numberText(cash)) < 0)) {
      found.push({ index: null, symbol: null, field: "cash", message: tr("เงินสดต้องเป็นตัวเลข ≥ 0", "Cash must be a number ≥ 0") });
    }
    return found;
  };
  const cashValue = () => (percent ? (book?.cash ?? null) : (numberText(cash) ?? null));
  /** rows of the add dialog → merged into what the table shows (WYSIWYG) → PUT */
  const saveAdded = async (added: PositionInput[]): Promise<EntryError[] | null> => {
    const found = check();
    if (found.length) {
      setErrors(found);
      return [
        {
          index: null,
          symbol: null,
          field: null,
          message: tr("แก้แถวในตาราง Holdings ที่มีปัญหาก่อน แล้วค่อยบันทึก", "Fix the rows with problems in the Holdings table first, then save"),
        },
      ];
    }
    const merged = mergePositions(buildPositions(rows), added);
    const result = await onSave(merged.positions, cashValue());
    if (result) return remapErrors(result, merged.indexOf);
    setErrors([]);
    setDirty(false);
    return null;
  };
  const save = async () => {
    const found = check();
    setErrors(found);
    if (found.length) return;
    setSaving(true);
    try {
      const result = await onSave(buildPositions(rows), cashValue());
      if (result) setErrors(result);
      else {
        setErrors([]);
        setDirty(false);
      }
    } finally {
      setSaving(false);
    }
  };

  const holdings = useMemo(() => new Map((analytics?.holdings ?? []).map((h) => [h.symbol, h])), [analytics]);
  const grouped = useMemo(() => groupErrors(errors), [errors]);
  const data = useMemo<Row[]>(
    () => rows.map((draft, index) => ({ draft, index, holding: holdings.get(draft.symbol) })),
    [rows, holdings],
  );

  const rowErr = (r: Row, field: string) => (grouped.rows.get(r.index) ?? []).some((e) => e.field === field);
  const currencyOf = (d: DraftRow) => symbols[d.key]?.currency ?? holdings.get(d.symbol)?.currency ?? null;

  /** the two inputs of a row's entry mode (Qty / Avg cost columns) */
  const draftInput = (r: Row, rowIndex: number, slot: "first" | "second") => {
    const d = r.draft;
    const spec = d.kind === "weight" ? MODE_SPEC.qty_cost : MODE_SPEC[d.kind];
    const { field, api } = slot === "first" ? spec.first : spec.second;
    return (
      <CellInput
        label={`${FIELD_ARIA[field]} ${d.symbol || rowIndex + 1}`}
        placeholder={d.isNew ? fieldLabel(field, currencyOf(d)) : undefined}
        value={d[field]}
        invalid={rowErr(r, api)}
        onChange={(v) => edit(d.key, fieldPatch(field, v))}
      />
    );
  };

  const columns = col.columns([
    col.accessor((r) => r.draft.symbol, {
      id: "symbol",
      header: "Symbol",
      enableHiding: false,
      cell: (c) => {
        const r = c.row.original;
        if (r.draft.isNew) {
          const picked = symbols[r.draft.key];
          return (
            <div className="min-w-44">
              <SymbolSearch
                value={picked ?? null}
                search={search}
                onPick={(o) => {
                  setSymbols((s) => ({ ...s, [r.draft.key]: o.symbol }));
                  edit(r.draft.key, { symbol: o.symbol.canonical });
                }}
              />
            </div>
          );
        }
        const h = r.holding;
        return (
          <div className="flex items-center gap-1.5">
            <span className="num font-medium">{h?.display ?? r.draft.symbol}</span>
            {h ? <AssetBadge symbol={h} /> : null}
            {h && h.status !== "ok" && h.status !== "declared" ? (
              <span className="rounded-sm bg-warning/15 px-1 text-[10.5px] text-warning">
                {h.status === "no_price" ? t("ไม่มีราคา", "no price") : t("ไม่มี FX", "no FX")}
              </span>
            ) : null}
          </div>
        );
      },
    }),
    col.display({
      id: "qty",
      header: "Qty",
      cell: (c) => draftInput(c.row.original, c.row.index, "first"),
    }),
    col.display({
      id: "avg",
      header: "Avg cost",
      cell: (c) => draftInput(c.row.original, c.row.index, "second"),
    }),
    col.display({
      id: "declared",
      header: t("% ที่กรอก", "Declared %"),
      cell: (c) => {
        const r = c.row.original;
        return (
          <CellInput
            label={`weight ${r.draft.symbol || c.row.index + 1}`}
            placeholder={t("เช่น 25", "e.g. 25")}
            value={r.draft.weightPct}
            invalid={rowErr(r, "weight")}
            onChange={(v) => edit(r.draft.key, { weightPct: v })}
          />
        );
      },
    }),
    col.accessor((r) => num(r.holding?.price), {
      id: "price",
      header: "Price",
      sortFn: "basic",
      sortUndefined: "last",
      cell: (c) => <ProvValue pv={c.row.original.holding?.price} />,
    }),
    col.accessor((r) => num(r.holding?.market_value), {
      id: "value",
      header: "Value",
      sortFn: "basic",
      sortUndefined: "last",
      cell: (c) => <ProvValue pv={c.row.original.holding?.market_value} />,
    }),
    col.accessor((r) => num(r.holding?.weight), {
      id: "weight",
      header: "Weight%",
      sortFn: "basic",
      sortUndefined: "last",
      cell: (c) => <ProvValue pv={c.row.original.holding?.weight} format="pct" />,
    }),
    col.display({
      id: "target",
      header: "Target%",
      cell: (c) => {
        const r = c.row.original;
        return (
          <CellInput
            label={`target ${r.draft.symbol || c.row.index + 1}`}
            value={r.draft.targetPct}
            invalid={rowErr(r, "target_weight")}
            onChange={(v) => edit(r.draft.key, { targetPct: v })}
          />
        );
      },
    }),
    col.accessor((r) => num(r.holding?.unrealized_pnl), {
      id: "pnl",
      header: "P&L",
      sortFn: "basic",
      sortUndefined: "last",
      cell: (c) => <ProvValue pv={c.row.original.holding?.unrealized_pnl} format="signed" colored />,
    }),
    col.accessor((r) => num(r.holding?.unrealized_pnl_pct), {
      id: "pnlPct",
      header: "P&L%",
      sortFn: "basic",
      sortUndefined: "last",
      cell: (c) => <ProvValue pv={c.row.original.holding?.unrealized_pnl_pct} format="signedPct" colored />,
    }),
    col.accessor((r) => r.holding?.sector ?? "", {
      id: "sector",
      header: "Sector",
      sortFn: "alphanumeric",
      cell: (c) => <span className="text-muted">{c.getValue() || "–"}</span>,
    }),
    col.display({
      id: "note",
      header: "Note",
      cell: (c) => {
        const r = c.row.original;
        return (
          <CellInput label={`note ${r.draft.symbol || c.row.index + 1}`} align="left" value={r.draft.note} onChange={(v) => edit(r.draft.key, { note: v })} />
        );
      },
    }),
    col.display({
      id: "actions",
      header: "",
      enableHiding: false,
      cell: (c) => {
        const d = c.row.original.draft;
        return (
          <div className="flex items-center justify-end gap-1">
            {d.isNew && d.kind !== "weight" ? (
              <select
                aria-label={t(`โหมดกรอก ${d.symbol || c.row.index + 1}`, `Entry mode ${d.symbol || c.row.index + 1}`)}
                data-testid="row-mode"
                title={t("คุณเห็นอะไรในแอปโบรกเกอร์?", "What does your broker app show?")}
                value={d.kind}
                onChange={(e) => {
                  const kind = e.target.value;
                  if (isGuidedMode(kind)) edit(d.key, { kind });
                }}
                className="max-w-32 rounded-sm border border-transparent bg-transparent px-0.5 text-[11px] text-accent outline-none hover:border-line focus:border-accent"
              >
                {ENTRY_MODES.map((m) => (
                  <option key={m} value={m}>
                    {modeLabel(m, currencyOf(d))}
                  </option>
                ))}
              </select>
            ) : null}
            <button
              type="button"
              aria-label={t(`ลบ ${d.symbol || "แถว"}`, `Remove ${d.symbol || "row"}`)}
              onClick={() => remove(d.key)}
              className="rounded-sm p-1 text-subtle hover:bg-error/15 hover:text-error"
            >
              <Trash2 className="size-3.5" aria-hidden />
            </button>
          </div>
        );
      },
    }),
  ]);

  const forced = Object.fromEntries((percent ? PERCENT_HIDDEN : QTY_HIDDEN).map((id) => [id, false]));
  const table = useTable({
    features,
    data,
    columns,
    getRowId: (r) => r.draft.key,
    state: { sorting, globalFilter: filter, columnVisibility: { ...hidden, ...forced } },
    onSortingChange: setSorting,
    onGlobalFilterChange: (u) => setFilter((prev) => String(typeof u === "function" ? u(prev) : (u ?? ""))),
    onColumnVisibilityChange: (u) =>
      setHidden((prev) => {
        const next = typeof u === "function" ? u({ ...prev, ...forced }) : u;
        return Object.fromEntries(Object.entries(next).filter(([k]) => !(k in forced)));
      }),
    globalFilterFn: "includesString",
  });

  const total = declaredTotalPct(rows);
  const ccy = analytics?.base_currency ?? book?.base_currency ?? "THB";

  return (
    <section data-testid="holdings-table" className="rounded-md border border-line bg-layer-1">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <h2 className="text-[13px] font-semibold">Holdings</h2>
        {percent ? (
          <span data-testid="declared-badge" className="rounded-sm bg-accent-muted px-1.5 text-[11px] text-accent">
            declared weights
          </span>
        ) : null}
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={t("กรอง symbol / sector…", "Filter symbol / sector…")}
          aria-label="filter holdings"
          className="w-40 rounded-sm border border-line bg-layer-2 px-2 py-0.5 text-[12px] outline-none"
        />
        <span className="flex-1" />
        {!percent ? (
          <label className="flex items-center gap-1 text-[12px] text-muted">
            {t("เงินสด", "Cash")} ({ccy})
            <input
              aria-label="cash"
              value={cash}
              placeholder={t("ไม่ระบุ", "not given")}
              inputMode="decimal"
              onChange={(e) => {
                setCash(e.target.value);
                setDirty(true);
              }}
              className="num w-28 rounded-sm border border-line bg-bg px-1.5 py-0.5 text-right text-[12.5px] text-fg outline-none focus:border-accent"
            />
          </label>
        ) : null}
        <div className="relative">
          <button
            type="button"
            onClick={() => setShowColumns((v) => !v)}
            className="inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[12px] text-muted hover:bg-layer-2"
          >
            <Columns3 className="size-3.5" aria-hidden /> {t("คอลัมน์", "Columns")}
          </button>
          {showColumns ? (
            <div className="absolute right-0 z-30 mt-1 w-40 rounded-md border border-line-strong bg-layer-1 p-1 shadow-lg">
              {table
                .getAllLeafColumns()
                .filter((c) => c.getCanHide() && !(c.id in forced))
                .map((c) => (
                  <label key={c.id} className="flex items-center gap-2 px-2 py-0.5 text-[12px]">
                    <input type="checkbox" checked={c.getIsVisible()} onChange={() => c.toggleVisibility()} />
                    {COLUMN_LABELS[c.id] ?? c.id}
                  </label>
                ))}
            </div>
          ) : null}
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]">
          <thead className="text-left text-[11.5px] text-subtle">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => (
                  <th
                    key={h.id}
                    onClick={h.column.getCanSort() ? h.column.getToggleSortingHandler() : undefined}
                    className={clsx(
                      "whitespace-nowrap px-2 py-1.5 font-normal",
                      h.column.getCanSort() && "cursor-pointer hover:text-fg",
                      h.id !== "symbol" && h.id !== "sector" && h.id !== "note" && "text-right",
                    )}
                  >
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    {{ asc: " ↑", desc: " ↓" }[h.column.getIsSorted() as string] ?? ""}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => {
              const rowErrors = grouped.rows.get(row.original.index) ?? [];
              return (
                <FragmentRow key={row.id} errors={rowErrors} span={row.getVisibleCells().length}>
                  <tr
                    data-testid="holding-row"
                    data-symbol={row.original.draft.symbol}
                    className={clsx("border-t border-line/60", rowErrors.length && "bg-error/5", row.original.draft.isNew && "bg-accent-muted/20")}
                  >
                    {row.getVisibleCells().map((cell) => (
                      <td
                        key={cell.id}
                        className={clsx(
                          "whitespace-nowrap px-2 py-1",
                          cell.column.id !== "symbol" && cell.column.id !== "sector" && cell.column.id !== "note" && "num text-right",
                        )}
                      >
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                  </tr>
                </FragmentRow>
              );
            })}
            {rows.length === 0 ? (
              <tr>
                <td colSpan={20} className="px-3 py-4 text-center text-[12.5px] text-subtle">
                  {t("ยังไม่มี position · กด “+ เพิ่ม” หรือ Import CSV", "No positions yet · press “+ Add” or Import CSV")}
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>

      {percent ? (
        <div
          data-testid="declared-total"
          className={clsx(
            "num border-t border-line px-3 py-1.5 text-[12px]",
            total > 100 + 1e-9 ? "text-error" : Math.abs(total - 100) > 1e-9 ? "text-warning" : "text-done",
          )}
        >
          {t("รวม", "Total")} {total.toFixed(2)}%
          {total > 100 + 1e-9
            ? t(" · เกิน 100% บันทึกไม่ได้", " · over 100%, cannot save")
            : Math.abs(total - 100) > 1e-9
              ? t(
                  ` · ไม่ครบ 100% — ส่วนต่าง ${(100 - total).toFixed(2)}% ถือเป็นเงินสด`,
                  ` · under 100% — the remaining ${(100 - total).toFixed(2)}% counts as cash`,
                )
              : t(" · ครบ 100%", " · exactly 100%")}
        </div>
      ) : null}
      {grouped.book.length ? (
        <ul data-testid="book-errors" className="border-t border-line px-3 py-1.5 text-[12px] text-error">
          {grouped.book.map((e, i) => (
            <li key={i}>{e.message}</li>
          ))}
        </ul>
      ) : null}

      <div className="flex flex-wrap items-center gap-2 border-t border-line px-3 py-2">
        <button type="button" data-testid="add-row" onClick={add} className="inline-flex items-center gap-1 rounded-md bg-layer-2 px-2 py-1 text-[12.5px]">
          <Plus className="size-3.5" aria-hidden /> {t("เพิ่ม", "Add")}
        </button>
        <button type="button" onClick={onImport} className="inline-flex items-center gap-1 rounded-md bg-layer-2 px-2 py-1 text-[12.5px]">
          <FileUp className="size-3.5" aria-hidden /> Import CSV
        </button>
        {onPaste ? (
          <button
            type="button"
            data-testid="paste-holdings"
            disabled={dirty}
            title={
              dirty
                ? t("บันทึกหรือยกเลิกการแก้ไขในตารางก่อน", "Save or discard the table edits first")
                : t("วางข้อความจากแอปโบรกเกอร์ ให้ AI แปลงเป็นแถว", "Paste text from your broker app and let the AI turn it into rows")
            }
            onClick={onPaste}
            className="inline-flex items-center gap-1 rounded-md bg-layer-2 px-2 py-1 text-[12.5px] disabled:opacity-50"
          >
            <ClipboardPaste className="size-3.5" aria-hidden /> {t("วางข้อความ", "Paste text")}
          </button>
        ) : null}
        <button
          type="button"
          data-testid="percent-mode"
          aria-pressed={percent}
          onClick={togglePercent}
          className={clsx("inline-flex items-center gap-1 rounded-md px-2 py-1 text-[12.5px]", percent ? "bg-accent-muted text-accent" : "bg-layer-2")}
        >
          <Percent className="size-3.5" aria-hidden /> {t("กรอกแบบ % อย่างเดียว", "Percent-only entry")}
        </button>
        <span className="flex-1" />
        {dirty ? (
          <>
            <button type="button" onClick={discard} className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[12.5px] text-muted hover:bg-layer-2">
              <RotateCcw className="size-3.5" aria-hidden /> {t("ยกเลิก", "Discard")}
            </button>
            <button
              type="button"
              data-testid="save-holdings"
              disabled={saving}
              onClick={() => void save()}
              className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1 text-[12.5px] text-bg disabled:opacity-60"
            >
              <Save className="size-3.5" aria-hidden /> {saving ? t("กำลังบันทึก…", "Saving…") : t("บันทึก", "Save")}
            </button>
          </>
        ) : (
          <span className="text-[11.5px] text-subtle">
            {book ? `${book.positions.length} positions · ${formatQty(book.cash)} ${t("เงินสด", "cash")}` : t("ยังไม่ได้บันทึก", "Not saved yet")}
          </span>
        )}
      </div>

      {adding ? (
        <AddPositionDialog
          portfolioId={portfolioId}
          onClose={() => setAdding(false)}
          onSave={saveAdded}
          onPercentMode={startPercent}
          api={addApi}
          search={search}
        />
      ) : null}
    </section>
  );
}

function FragmentRow({ children, errors, span }: { children: React.ReactNode; errors: EntryError[]; span: number }) {
  return (
    <>
      {children}
      {errors.length ? (
        <tr data-testid="row-error">
          <td colSpan={span} className="px-3 pb-1.5 text-[11.5px] text-error">
            {errors.map((e) => e.message).join(" · ")}
          </td>
        </tr>
      ) : null}
    </>
  );
}
