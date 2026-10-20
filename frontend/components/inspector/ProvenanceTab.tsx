"use client";

/**
 * Provenance records of a run (GET /runs/{id}/provenance) in a sortable,
 * filterable table (@tanstack/react-table); selecting a record loads its
 * lineage tree lazily (GET /provenance/{pid}?lineage=1). A citation click
 * focuses its record and scrolls it into view.
 */

import {
  columnFilteringFeature,
  createColumnHelper,
  createFilteredRowModel,
  createSortedRowModel,
  filterFn_includesString,
  flexRender,
  globalFilteringFeature,
  rowSortingFeature,
  sortFn_alphanumeric,
  tableFeatures,
  useTable,
  type SortingState,
} from "@tanstack/react-table";
import { clsx } from "clsx";
import { ChevronRight } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { api, type LineageNode, type ProvenanceRecord } from "@/lib/api";
import { formatValue, shortId } from "@/lib/format";
import { useT } from "@/lib/i18n";
import type { RunView } from "@/stores/run-reducer";
import { useUiStore } from "@/stores/ui-store";

// TanStack Table v9: features and row models are registered explicitly
const features = tableFeatures({
  rowSortingFeature,
  columnFilteringFeature,
  globalFilteringFeature,
  filteredRowModel: createFilteredRowModel(),
  sortedRowModel: createSortedRowModel(),
  filterFns: { includesString: filterFn_includesString },
  sortFns: { alphanumeric: sortFn_alphanumeric },
});

const col = createColumnHelper<typeof features, ProvenanceRecord>();
const COLUMNS = col.columns([
  col.accessor("id", { header: "id", cell: (c) => shortId(c.getValue(), 5) }),
  col.accessor((r) => formatValue(r.value, 40), { id: "value", header: "value" }),
  col.accessor((r) => r.unit ?? "", { id: "unit", header: "unit" }),
  col.accessor("source", { header: "source" }),
  col.accessor((r) => r.tool ?? "", { id: "tool", header: "tool" }),
  col.accessor((r) => (r.as_of ?? "").slice(0, 10), { id: "as_of", header: "as of" }),
  col.accessor("kind", { header: "kind" }),
]);

function LineageTree({ node, depth = 0 }: { node: LineageNode; depth?: number }) {
  const [open, setOpen] = useState(depth < 2);
  const r = node.record;
  const hasKids = node.inputs.length > 0 || node.missing_inputs.length > 0;
  return (
    <div style={{ paddingLeft: depth ? 12 : 0 }} className={clsx(depth > 0 && "border-l border-line")}>
      <button type="button" onClick={() => setOpen((v) => !v)} className="flex w-full items-start gap-1 py-0.5 text-left">
        <ChevronRight className={clsx("mt-0.5 size-3 shrink-0 text-subtle", open && "rotate-90", !hasKids && "invisible")} />
        <span className="min-w-0">
          <span className="num block break-all text-fg">
            {formatValue(r.value, 80)} {r.unit ?? ""}
          </span>
          <span className="block text-[11px] text-muted">
            {r.kind} · {r.source}
            {r.tool ? ` · ${r.tool}` : ""}
            {r.as_of ? ` · ${r.as_of.slice(0, 10)}` : ""}
          </span>
          {r.calculation ? <span className="num block text-[11px] text-accent">{r.calculation}</span> : null}
        </span>
      </button>
      {open ? (
        <>
          {node.inputs.map((child) => (
            <LineageTree key={child.record.id} node={child} depth={depth + 1} />
          ))}
          {node.missing_inputs.map((id) => (
            <div key={id} className="num pl-4 text-[11px] text-error">missing {shortId(id)}</div>
          ))}
        </>
      ) : null}
    </div>
  );
}

export function Lineage({ pid }: { pid: string }) {
  const t = useT();
  const [tree, setTree] = useState<LineageNode | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    setTree(undefined);
    api
      .lineage(pid)
      .then((r) => alive && setTree(r.lineage))
      .catch(() => alive && setTree(null));
    return () => {
      alive = false;
    };
  }, [pid]);
  if (tree === undefined) return <div className="text-subtle">{t("กำลังโหลด lineage…", "Loading lineage…")}</div>;
  if (tree === null) return <div className="text-error">{t("โหลด lineage ไม่ได้", "Could not load lineage")}</div>;
  return <LineageTree node={tree} />;
}

export function ProvenanceTab({ view }: { view: RunView }) {
  const t = useT();
  const [records, setRecords] = useState<ProvenanceRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sorting, setSorting] = useState<SortingState>([]);
  const [filter, setFilter] = useState("");
  const focus = useUiStore((s) => s.focusProvenanceId);
  const [selected, setSelected] = useState<string | null>(focus);
  const rowRefs = useRef(new Map<string, HTMLTableRowElement>());
  const finishedCalls = Object.values(view.calls).filter((c) => c.finishedAt !== null).length;

  useEffect(() => {
    let alive = true;
    api
      .provenance(view.runId)
      .then((r) => alive && (setRecords(r.records), setError(null)))
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [view.runId, finishedCalls, view.status]);

  useEffect(() => {
    if (!focus) return;
    setSelected(focus);
    setFilter("");
    requestAnimationFrame(() => rowRefs.current.get(focus)?.scrollIntoView({ block: "center" }));
  }, [focus, records]);

  const data = useMemo(() => records ?? [], [records]);
  const table = useTable({
    features,
    data,
    columns: COLUMNS,
    state: { sorting, globalFilter: filter },
    onSortingChange: setSorting,
    onGlobalFilterChange: (updater) => setFilter((prev) => String(typeof updater === "function" ? updater(prev) : (updater ?? ""))),
    globalFilterFn: "includesString",
  });

  return (
    <div data-testid="provenance-tab" className="text-[12px]">
      <div className="sticky top-0 z-10 flex items-center gap-2 border-b border-line bg-layer-1 p-2">
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder={t("กรอง value / source / tool…", "Filter value / source / tool…")}
          className="min-w-0 flex-1 rounded-sm border border-line bg-layer-2 px-2 py-0.5 outline-none"
        />
        <span className="num text-subtle">{table.getRowModel().rows.length}/{data.length}</span>
      </div>
      {error ? <div className="p-3 text-error">{error}</div> : null}
      {records === null && !error ? <div className="p-3 text-subtle">{t("กำลังโหลด…", "Loading…")}</div> : null}
      {selected ? (
        <div className="border-b border-line bg-layer-2/40 p-2">
          <div className="mb-1 flex items-center justify-between text-[11px] uppercase tracking-wide text-subtle">
            Lineage · <span className="num normal-case">{shortId(selected)}</span>
            <button type="button" onClick={() => setSelected(null)} className="normal-case text-accent">{t("ปิด", "Close")}</button>
          </div>
          <Lineage pid={selected} />
        </div>
      ) : null}
      <div className="overflow-x-auto">
        <table className="num w-full">
          <thead className="text-left text-subtle">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => (
                  <th key={h.id} className="cursor-pointer whitespace-nowrap px-2 py-1 font-normal hover:text-fg" onClick={h.column.getToggleSortingHandler()}>
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    {{ asc: " ↑", desc: " ↓" }[h.column.getIsSorted() as string] ?? ""}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                ref={(el) => {
                  if (el) rowRefs.current.set(row.original.id, el);
                  else rowRefs.current.delete(row.original.id);
                }}
                data-selected={row.original.id === selected}
                onClick={() => setSelected(row.original.id)}
                className={clsx(
                  "cursor-pointer border-t border-line/60 hover:bg-layer-2",
                  row.original.id === selected && "bg-accent-muted",
                )}
              >
                {row.getAllCells().map((cell) => (
                  <td key={cell.id} className="max-w-40 truncate px-2 py-1 text-muted">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
