"use client";

/** `[n]` chip on a cited number: hover → provenance popover, click → inspector Provenance tab. */

import { useEffect, useState } from "react";

import { api, type ProvenanceRecord } from "@/lib/api";
import { formatValue } from "@/lib/format";
import { useUiStore } from "@/stores/ui-store";

const cache = new Map<string, Promise<ProvenanceRecord | null>>();

export function loadProvenance(pid: string): Promise<ProvenanceRecord | null> {
  let hit = cache.get(pid);
  if (!hit) {
    hit = api
      .provenanceRecord(pid)
      .then((r) => r.record)
      .catch(() => {
        cache.delete(pid);
        return null;
      });
    cache.set(pid, hit);
  }
  return hit;
}

export function CitationChip({
  marker,
  provenanceId,
  runId,
  load = loadProvenance,
}: {
  marker: number;
  provenanceId: string;
  runId: string;
  load?: (pid: string) => Promise<ProvenanceRecord | null>;
}) {
  const [hover, setHover] = useState(false);
  const [record, setRecord] = useState<ProvenanceRecord | null | undefined>(undefined);
  const focusProvenance = useUiStore((s) => s.focusProvenance);

  useEffect(() => {
    if (!hover || record !== undefined) return;
    let alive = true;
    load(provenanceId).then((r) => alive && setRecord(r));
    return () => {
      alive = false;
    };
  }, [hover, record, provenanceId, load]);

  return (
    <span className="relative inline-block" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      <button
        type="button"
        data-testid="citation-chip"
        onClick={() => focusProvenance(runId, provenanceId)}
        onFocus={() => setHover(true)}
        onBlur={() => setHover(false)}
        className="num mx-0.5 rounded-sm bg-accent-muted px-1 align-super text-[10px] leading-none text-accent hover:bg-accent hover:text-bg"
        aria-label={`แหล่งที่มา ${marker}`}
      >
        {marker}
      </button>
      {hover ? (
        <span
          role="tooltip"
          data-testid="citation-popover"
          className="absolute bottom-full left-1/2 z-30 mb-1 block w-72 -translate-x-1/2 rounded-md border border-line-strong bg-layer-1 p-2 text-left text-xs shadow-lg"
        >
          {record === undefined ? (
            <span className="text-subtle">กำลังโหลด…</span>
          ) : record === null ? (
            <span className="text-error">ไม่พบ provenance {provenanceId}</span>
          ) : (
            <span className="block space-y-0.5">
              <span className="num block break-words text-fg">
                {formatValue(record.value, 160)} {record.unit ?? ""}
              </span>
              <span className="block text-muted">
                {record.kind} · {record.source}
                {record.tool ? ` · ${record.tool}` : ""}
              </span>
              {record.as_of ? <span className="block text-subtle">as of {record.as_of}</span> : null}
              {record.calculation ? <span className="num block text-subtle">{record.calculation}</span> : null}
              <span className="block pt-1 text-[10px] text-subtle">คลิกเพื่อดู lineage</span>
            </span>
          )}
        </span>
      ) : null}
    </span>
  );
}
