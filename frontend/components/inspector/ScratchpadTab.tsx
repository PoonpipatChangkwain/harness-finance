"use client";

/**
 * Scratchpad entries of a run (GET /runs/{id}/scratchpad): type badge, tool,
 * time, filter by tool/type, expand to JSON, download as JSONL. Refetched
 * while the run is live as tool results land.
 */

import { clsx } from "clsx";
import { ChevronRight, Download } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { JsonView } from "@/components/ui/JsonView";
import { api, type ScratchpadEntry } from "@/lib/api";
import { formatDuration, formatTime } from "@/lib/format";
import { useT } from "@/lib/i18n";
import type { RunView } from "@/stores/run-reducer";

const TYPE_COLOR: Record<string, string> = {
  init: "text-accent",
  tool_result: "text-done",
  thinking: "text-muted",
  compaction: "text-warning",
  final: "text-accent",
};

export function ScratchpadTab({ view }: { view: RunView }) {
  const t = useT();
  const [entries, setEntries] = useState<ScratchpadEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tool, setTool] = useState("");
  const [type, setType] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const finishedCalls = Object.values(view.calls).filter((c) => c.finishedAt !== null).length;

  useEffect(() => {
    let alive = true;
    api
      .scratchpad(view.runId, { limit: 1000 })
      .then((page) => alive && (setEntries(page.entries), setError(null)))
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, [view.runId, finishedCalls, view.status]);

  const tools = useMemo(() => [...new Set((entries ?? []).map((e) => e.tool).filter(Boolean))] as string[], [entries]);
  const types = useMemo(() => [...new Set((entries ?? []).map((e) => e.type))], [entries]);
  const shown = (entries ?? []).filter((e) => (!tool || e.tool === tool) && (!type || e.type === type));

  const download = () => {
    const blob = new Blob([(entries ?? []).map((e) => JSON.stringify(e)).join("\n") + "\n"], { type: "application/jsonl" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${view.runId}.scratchpad.jsonl`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div data-testid="scratchpad-tab" className="text-[12.5px]">
      <div className="sticky top-0 flex flex-wrap items-center gap-1.5 border-b border-line bg-layer-1 p-2">
        <select aria-label="Filter by type" value={type} onChange={(e) => setType(e.target.value)} className="rounded-sm border border-line bg-layer-2 px-1 py-0.5">
          <option value="">{t("ทุก type", "All types")}</option>
          {types.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
        <select aria-label="Filter by tool" value={tool} onChange={(e) => setTool(e.target.value)} className="rounded-sm border border-line bg-layer-2 px-1 py-0.5">
          <option value="">{t("ทุก tool", "All tools")}</option>
          {tools.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
        <span className="num text-subtle">{shown.length}/{entries?.length ?? 0}</span>
        <span className="flex-1" />
        <button type="button" onClick={download} disabled={!entries?.length} className="inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-accent hover:bg-layer-2 disabled:text-subtle">
          <Download className="size-3.5" aria-hidden /> JSONL
        </button>
      </div>
      {error ? <div className="p-3 text-error">{error}</div> : null}
      {entries === null && !error ? <div className="p-3 text-subtle">{t("กำลังโหลด…", "Loading…")}</div> : null}
      <ul>
        {shown.map((e, i) => (
          <li key={i} className="border-b border-line/60">
            <button type="button" onClick={() => setOpen(open === i ? null : i)} className="flex w-full items-center gap-2 px-2 py-1 text-left hover:bg-layer-2">
              <ChevronRight className={clsx("size-3 shrink-0 text-subtle", open === i && "rotate-90")} />
              <span className={clsx("num shrink-0 text-[11px]", TYPE_COLOR[e.type] ?? "text-muted")}>{e.type}</span>
              <span className="num min-w-0 flex-1 truncate text-fg">{e.tool ?? String(e.kind ?? "")}</span>
              {typeof e.duration_ms === "number" ? <span className="num text-[11px] text-subtle">{formatDuration(e.duration_ms)}</span> : null}
              <span className="num shrink-0 text-[11px] text-subtle">{formatTime(e.ts)}</span>
            </button>
            {open === i ? (
              <div className="bg-layer-2/50 px-2 py-1">
                <JsonView data={e} />
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}
