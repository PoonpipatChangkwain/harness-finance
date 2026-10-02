"use client";

/** Inspector "State" (ui-spec §5): latest graph state from the checkpoint (`/runs/{id}/state`). */

import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { JsonView } from "@/components/ui/JsonView";
import { tradeApi } from "@/lib/trade-api";

type StateResponse = Awaited<ReturnType<typeof tradeApi.state>>;

export function StateTab({
  runId,
  refreshKey = "",
  fallback,
  load = tradeApi.state,
}: {
  runId: string;
  refreshKey?: string;
  /** shown when the checkpoint is gone (a successful trading run clears it, T3) */
  fallback?: unknown;
  load?: (runId: string) => Promise<StateResponse>;
}) {
  const [state, setState] = useState<StateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    load(runId)
      .then((s) => {
        setState(s);
        setError(null);
      })
      .catch((err: unknown) => {
        setState(null);
        setError(err instanceof Error ? err.message : String(err));
      });
  }, [runId, load]);

  useEffect(refresh, [refresh, refreshKey]);

  return (
    <div data-testid="state-tab" className="space-y-2 p-3 text-[12.5px]">
      <div className="flex items-center gap-2">
        <span className="text-subtle">thread</span>
        <span className="num">{state?.thread_id ?? "–"}</span>
        {state?.next.length ? <span className="text-muted">next: {state.next.join(", ")}</span> : null}
        <button type="button" onClick={refresh} className="ml-auto rounded-md p-1 text-subtle hover:bg-layer-2" aria-label="refresh state">
          <RefreshCw className="size-3.5" />
        </button>
      </div>
      {state ? (
        Object.keys(state.values).length ? (
          <JsonView data={state.values} />
        ) : (
          <p className="text-subtle">checkpoint ว่าง (run จบแล้วและล้าง checkpoint ตาม T3)</p>
        )
      ) : error ? (
        <p className="text-subtle">ไม่มี checkpoint: {error}</p>
      ) : (
        <p className="text-subtle">กำลังโหลด…</p>
      )}
      {fallback && (!state || !Object.keys(state.values).length) ? (
        <section>
          <h4 className="mb-1 text-[11px] uppercase tracking-wide text-subtle">State สุดท้าย (จาก output ของ run)</h4>
          <JsonView data={fallback} />
        </section>
      ) : null}
    </div>
  );
}
