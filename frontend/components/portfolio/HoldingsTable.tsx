"use client";

/**
 * Holdings table with inline editing (phase-13 §13.2, ui-spec §6).
 * - qty / avg cost / target % / note are edited in place; new rows are drafts (symbol via
 *   SymbolSearch, entered as quantity or as "value + % gain") until "บันทึก" PUTs the book
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
import { Columns3, FileUp, Percent, Plus, RotateCcw, Save, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { AssetBadge, SymbolSearch } from "@/components/trade/SymbolSearch";
import type { SymbolInfo } from "@/lib/api";
import {
  buildPositions,
  convertRows,
  declaredTotalPct,
  emptyRow,
  groupErrors,
  isPercentBook,
  rowsFromBook,
  validateRows,
  type DraftRow,
  type EntryKind,
} from "@/lib/holdings";
import type { EntryError, HoldingAnalytics, PortfolioAnalytics, PortfolioData, PositionInput } from "@/lib/portfolio-api";
import { formatQty, numberText } from "@/lib/portfolio-format";

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

const COLUMN_LABELS: Record<string, string> = {
  symbol: "Symbol",
  qty: "Qty",
  avg: "Avg cost",
  declared: "% ที่กรอก",
  price: "Price",
  value: "Value",
  weight: "Weight%",
  target: "Target%",
  pnl: "P&L",
  pnlPct: "P&L%",
  sector: "Sector",
  note: "Note",
};
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

export function HoldingsTable({
  book,
  analytics,
  onSave,
  onImport,
  startWith,
  search,
}: {
  book: PortfolioData | null;
  analytics: PortfolioAnalytics | null;
  /** PUT the book; resolves to per-row errors (422) or null when saved */
  onSave: (positions: PositionInput[], cash: string | null) => Promise<EntryError[] | null>;
  onImport: () => void;
  /** empty-state buttons: open a draft row of this kind (nonce re-triggers) */
  startWith?: { kind: EntryKind; nonce: number } | null;
  search?: SymbolSearchFn;
}) {
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

  // a saved book (or another book) replaces the drafts unless the user is mid-edit
  const bookKey = `${book?.id ?? "new"}:${book?.updated_at ?? ""}`;
  useEffect(() => {
    if (dirty) return;
    setRows(rowsFromBook(book));
    setPercent(isPercentBook(book));
    setCash(book?.cash ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only when the saved book changes
  }, [bookKey]);

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
    setRows((prev) => [...prev, emptyRow(percent ? "weight" : "value")]);
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
  const save = async () => {
    const found = validateRows(rows);
    if (!percent && cash.trim() && (numberText(cash) === undefined || Number(numberText(cash)) < 0)) {
      found.push({ index: null, symbol: null, field: "cash", message: "เงินสดต้องเป็นตัวเลข ≥ 0" });
    }
    setErrors(found);
    if (found.length) return;
    setSaving(true);
    try {
      const cashValue = percent ? (book?.cash ?? null) : (numberText(cash) ?? null);
      const result = await onSave(buildPositions(rows), cashValue);
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
                {h.status === "no_price" ? "ไม่มีราคา" : "ไม่มี FX"}
              </span>
            ) : null}
          </div>
        );
      },
    }),
    col.display({
      id: "qty",
      header: "Qty",
      cell: (c) => {
        const r = c.row.original;
        const d = r.draft;
        if (d.kind === "value") {
          return (
            <CellInput
              label={`value ${d.symbol || c.row.index + 1}`}
              placeholder={`มูลค่า${symbols[d.key] ? ` (${symbols[d.key]!.currency})` : ""}`}
              value={d.value}
              invalid={rowErr(r, "value")}
              onChange={(v) => edit(d.key, { value: v })}
            />
          );
        }
        return (
          <CellInput
            label={`quantity ${d.symbol || c.row.index + 1}`}
            value={d.quantity}
            invalid={rowErr(r, "quantity")}
            onChange={(v) => edit(d.key, { quantity: v })}
          />
        );
      },
    }),
    col.display({
      id: "avg",
      header: "Avg cost",
      cell: (c) => {
        const r = c.row.original;
        const d = r.draft;
        if (d.kind === "value") {
          return (
            <CellInput
              label={`pnl percent ${d.symbol || c.row.index + 1}`}
              placeholder="% กำไร เช่น 14.5"
              value={d.pnlPct}
              invalid={rowErr(r, "pnl_pct")}
              onChange={(v) => edit(d.key, { pnlPct: v })}
            />
          );
        }
        return (
          <CellInput
            label={`avg cost ${d.symbol || c.row.index + 1}`}
            value={d.avgCost}
            invalid={rowErr(r, "avg_cost")}
            onChange={(v) => edit(d.key, { avgCost: v })}
          />
        );
      },
    }),
    col.display({
      id: "declared",
      header: "% ที่กรอก",
      cell: (c) => {
        const r = c.row.original;
        return (
          <CellInput
            label={`weight ${r.draft.symbol || c.row.index + 1}`}
            placeholder="เช่น 25"
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
              <button
                type="button"
                title="สลับการกรอก: จำนวนหุ้น ⇄ มูลค่า + % กำไร"
                onClick={() => edit(d.key, { kind: d.kind === "qty" ? "value" : "qty" })}
                className="rounded-sm px-1 text-[11px] text-accent hover:bg-layer-2"
              >
                {d.kind === "qty" ? "qty" : "มูลค่า"}
              </button>
            ) : null}
            <button
              type="button"
              aria-label={`ลบ ${d.symbol || "แถว"}`}
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
          placeholder="กรอง symbol / sector…"
          aria-label="filter holdings"
          className="w-40 rounded-sm border border-line bg-layer-2 px-2 py-0.5 text-[12px] outline-none"
        />
        <span className="flex-1" />
        {!percent ? (
          <label className="flex items-center gap-1 text-[12px] text-muted">
            เงินสด ({ccy})
            <input
              aria-label="cash"
              value={cash}
              placeholder="ไม่ระบุ"
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
            <Columns3 className="size-3.5" aria-hidden /> คอลัมน์
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
                  ยังไม่มี position · กด “+ เพิ่ม” หรือ Import CSV
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
          รวม {total.toFixed(2)}%
          {total > 100 + 1e-9
            ? " · เกิน 100% บันทึกไม่ได้"
            : Math.abs(total - 100) > 1e-9
              ? ` · ไม่ครบ 100% — ส่วนต่าง ${(100 - total).toFixed(2)}% ถือเป็นเงินสด`
              : " · ครบ 100%"}
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
          <Plus className="size-3.5" aria-hidden /> เพิ่ม
        </button>
        <button type="button" onClick={onImport} className="inline-flex items-center gap-1 rounded-md bg-layer-2 px-2 py-1 text-[12.5px]">
          <FileUp className="size-3.5" aria-hidden /> Import CSV
        </button>
        <button
          type="button"
          data-testid="percent-mode"
          aria-pressed={percent}
          onClick={togglePercent}
          className={clsx("inline-flex items-center gap-1 rounded-md px-2 py-1 text-[12.5px]", percent ? "bg-accent-muted text-accent" : "bg-layer-2")}
        >
          <Percent className="size-3.5" aria-hidden /> กรอกแบบ % อย่างเดียว
        </button>
        <span className="flex-1" />
        {dirty ? (
          <>
            <button type="button" onClick={discard} className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[12.5px] text-muted hover:bg-layer-2">
              <RotateCcw className="size-3.5" aria-hidden /> ยกเลิก
            </button>
            <button
              type="button"
              data-testid="save-holdings"
              disabled={saving}
              onClick={() => void save()}
              className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1 text-[12.5px] text-bg disabled:opacity-60"
            >
              <Save className="size-3.5" aria-hidden /> {saving ? "กำลังบันทึก…" : "บันทึก"}
            </button>
          </>
        ) : (
          <span className="text-[11.5px] text-subtle">
            {book ? `${book.positions.length} positions · ${formatQty(book.cash)} เงินสด` : "ยังไม่ได้บันทึก"}
          </span>
        )}
      </div>
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
