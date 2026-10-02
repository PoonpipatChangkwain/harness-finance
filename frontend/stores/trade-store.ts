/**
 * Trade run details (`GET /trade/runs/{id}`) shared by the Trade page and its
 * inspector tabs (P11). Events drive the live graph; the detail carries the
 * per-node results (reports, debates, decision, policy) and is refetched each
 * time a node finishes or an approval changes.
 */

"use client";

import { useEffect } from "react";
import { create } from "zustand";

import { ApiError } from "@/lib/api";
import { tradeApi, type GraphView, type TradeRunBrief, type TradeRunDetail } from "@/lib/trade-api";
import type { RunView } from "@/stores/run-reducer";
import { useRun } from "@/stores/run-store";

interface TradeStoreState {
  details: Record<string, TradeRunDetail>;
  errors: Record<string, string>;
  graph: GraphView | null;
  load: (runId: string) => Promise<void>;
  loadGraph: () => Promise<void>;
}

export const useTradeStore = create<TradeStoreState>((set, get) => ({
  details: {},
  errors: {},
  graph: null,
  load: async (runId) => {
    try {
      const detail = await tradeApi.detail(runId);
      set((s) => ({ details: { ...s.details, [runId]: detail }, errors: { ...s.errors, [runId]: "" } }));
    } catch (err) {
      set((s) => ({ errors: { ...s.errors, [runId]: err instanceof Error ? err.message : String(err) } }));
    }
  },
  loadGraph: async () => {
    if (get().graph) return;
    try {
      set({ graph: await tradeApi.graph() });
    } catch {
      // the page falls back to the run detail's graph_view
    }
  },
}));

interface TradeHistoryState {
  runs: TradeRunBrief[];
  loaded: boolean;
  error: string | null;
  symbol: string;
  setSymbol: (symbol: string) => void;
  reload: () => Promise<void>;
  /** DELETE /trade/runs/{id}; resolves to an error message, or null when deleted */
  remove: (id: string) => Promise<string | null>;
}

/** Sidebar history of trade runs (`/trade/runs`, optional symbol filter). */
export const useTradeHistory = create<TradeHistoryState>((set, get) => ({
  runs: [],
  loaded: false,
  error: null,
  symbol: "",
  setSymbol: (symbol) => {
    set({ symbol });
    void get().reload();
  },
  reload: async () => {
    const symbol = get().symbol.trim();
    try {
      const { runs } = await tradeApi.list(symbol || undefined);
      if (get().symbol.trim() === symbol) set({ runs, loaded: true, error: null });
    } catch (err) {
      set({ loaded: true, error: err instanceof Error ? err.message : String(err) });
    }
  },
  remove: async (id) => {
    try {
      await tradeApi.delete(id);
      set((s) => ({ runs: s.runs.filter((r) => r.run_id !== id) }));
      return null;
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) return "run นี้กำลังทำงานอยู่ กด Stop ก่อนแล้วค่อยลบ";
      return err instanceof Error ? err.message : String(err);
    }
  },
}));

/** Changes whenever a node finishes, starts again or an approval is raised/answered. */
export function detailKey(view: RunView | undefined): string {
  if (!view) return "";
  const nodes = Object.values(view.nodes)
    .map((n) => `${n.node}:${n.status}:${n.runs}`)
    .join(",");
  return `${view.status}|${nodes}|${Object.values(view.approvals).map((a) => a.outcome).join(",")}`;
}

export function useTradeDetail(runId: string | null | undefined): TradeRunDetail | undefined {
  const view = useRun(runId);
  const key = detailKey(view);
  const load = useTradeStore((s) => s.load);
  useEffect(() => {
    if (!runId) return;
    const t = window.setTimeout(() => void load(runId), key ? 250 : 0);
    return () => window.clearTimeout(t);
  }, [runId, key, load]);
  return useTradeStore((s) => (runId ? s.details[runId] : undefined));
}
