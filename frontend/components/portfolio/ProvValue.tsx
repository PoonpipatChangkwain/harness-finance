"use client";

/**
 * A computed number with its provenance (ui-spec §6: "hover ตัวเลขแล้วเห็น provenance").
 * Hover/focus → popover of the record (value, source, calculation, as of); click → the
 * inspector's lineage tab. A value without a provenance id renders as plain text.
 */

import { clsx } from "clsx";
import { useEffect, useState, type ReactNode } from "react";

import { loadProvenance } from "@/components/conversation/CitationChip";
import type { ProvenanceRecord } from "@/lib/api";
import { formatValue } from "@/lib/format";
import { formatMoney, formatPct, formatSigned, upDownClass } from "@/lib/portfolio-format";
import type { PV } from "@/lib/portfolio-api";
import { useUiStore } from "@/stores/ui-store";

export type ValueFormat = "money" | "signed" | "pct" | "signedPct" | "number" | "ratio";

export function formatAs(value: number | null | undefined, format: ValueFormat, digits?: number): string {
  switch (format) {
    case "money":
      return formatMoney(value, digits ?? 2);
    case "signed":
      return formatSigned(value, digits ?? 2);
    case "pct":
      return formatPct(value, digits ?? 2);
    case "signedPct":
      return formatPct(value, digits ?? 2, true);
    case "ratio":
      return value === null || value === undefined ? "–" : value.toFixed(digits ?? 3);
    default:
      return value === null || value === undefined ? "–" : value.toLocaleString("en-US", { maximumFractionDigits: digits ?? 2 });
  }
}

export function ProvValue({
  pv,
  value,
  provenanceId,
  format = "money",
  digits,
  colored = false,
  suffix,
  className,
  load = loadProvenance,
}: {
  pv?: PV | null;
  /** a number without its own PV (e.g. one cell of a matrix) + the record it comes from */
  value?: number | null;
  provenanceId?: string | null;
  format?: ValueFormat;
  digits?: number;
  colored?: boolean;
  suffix?: ReactNode;
  className?: string;
  load?: (pid: string) => Promise<ProvenanceRecord | null>;
}) {
  const v = pv ? pv.value : (value ?? null);
  const pid = pv?.provenance_id ?? provenanceId ?? null;
  const [hover, setHover] = useState(false);
  const [record, setRecord] = useState<ProvenanceRecord | null | undefined>(undefined);
  const focusRecord = useUiStore((s) => s.focusRecord);

  useEffect(() => {
    if (!hover || !pid || record !== undefined) return;
    let alive = true;
    load(pid).then((r) => alive && setRecord(r));
    return () => {
      alive = false;
    };
  }, [hover, pid, record, load]);

  const text = (
    <>
      {formatAs(v, format, digits)}
      {suffix}
    </>
  );
  const tone = colored ? upDownClass(v) : undefined;
  if (!pid || v === null) {
    return <span className={clsx("num", tone, className)}>{text}</span>;
  }
  return (
    <span className="relative inline-block" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      <button
        type="button"
        data-testid="prov-value"
        data-provenance-id={pid}
        onClick={() => focusRecord(pid)}
        onFocus={() => setHover(true)}
        onBlur={() => setHover(false)}
        className={clsx("num cursor-help underline decoration-dotted decoration-1 underline-offset-2", tone, className)}
      >
        {text}
      </button>
      {hover ? (
        <span
          role="tooltip"
          data-testid="prov-popover"
          className="absolute left-0 top-full z-40 mt-1 block w-72 rounded-md border border-line-strong bg-layer-1 p-2 text-left text-xs font-normal text-fg shadow-lg"
        >
          {record === undefined ? (
            <span className="text-subtle">กำลังโหลด…</span>
          ) : record === null ? (
            <span className="text-error">ไม่พบ provenance {pid}</span>
          ) : (
            <span className="block space-y-0.5">
              <span className="num block break-words">
                {formatValue(record.value, 160)} {record.unit ?? ""}
              </span>
              <span className="block text-muted">
                {record.kind} · {record.source}
                {record.tool ? ` · ${record.tool}` : ""}
              </span>
              {record.as_of ? <span className="block text-subtle">as of {record.as_of}</span> : null}
              {record.calculation ? <span className="num block text-accent">{record.calculation}</span> : null}
              <span className="block pt-1 text-[10px] text-subtle">คลิกเพื่อดู lineage</span>
            </span>
          )}
        </span>
      ) : null}
    </span>
  );
}
