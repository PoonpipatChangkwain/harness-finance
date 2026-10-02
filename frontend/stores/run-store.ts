/**
 * Zustand store of RunViews, one per run, fed by SSE (phase-08 §8.1).
 *
 * `useRun(runId)` subscribes automatically (replay from seq 0, then live) and
 * shares one stream per run between components. Events arriving in the same
 * task are applied in one store update so a replay renders once, not per event.
 */

"use client";

import { useEffect } from "react";
import { create } from "zustand";

import type { EventEnvelope } from "@/lib/events";
import { subscribeRun, type ConnStatus } from "@/lib/sse";

import { emptyRunView, flushBuffered, ingest, isTerminal, type RunView } from "./run-reducer";

export { applyEvent, emptyRunView, ingest, replay, type RunView } from "./run-reducer";

export type RunConn = ConnStatus | "idle";

interface RunStoreState {
  runs: Record<string, RunView>;
  conn: Record<string, RunConn>;
  ingestMany: (runId: string, events: EventEnvelope[]) => void;
  setConn: (runId: string, status: RunConn) => void;
  flush: (runId: string) => void;
}

export const useRunStore = create<RunStoreState>((set) => ({
  runs: {},
  conn: {},
  ingestMany: (runId, events) =>
    set((s) => ({ runs: { ...s.runs, [runId]: events.reduce(ingest, s.runs[runId] ?? emptyRunView(runId)) } })),
  setConn: (runId, status) => set((s) => ({ conn: { ...s.conn, [runId]: status } })),
  flush: (runId) =>
    set((s) => {
      const view = s.runs[runId];
      return view && view.buffered.length ? { runs: { ...s.runs, [runId]: flushBuffered(view) } } : s;
    }),
}));

interface Subscription {
  controller: AbortController;
  refs: number;
  done: boolean;
}

const subscriptions = new Map<string, Subscription>();
const queues = new Map<string, EventEnvelope[]>();

function enqueue(runId: string, e: EventEnvelope) {
  let queue = queues.get(runId);
  if (!queue) {
    queue = [];
    queues.set(runId, queue);
    queueMicrotask(() => {
      const batch = queues.get(runId) ?? [];
      queues.delete(runId);
      useRunStore.getState().ingestMany(runId, batch);
    });
  }
  queue.push(e);
}

/** Start (or share) the stream of one run. Returns the release function. */
export function retainRun(runId: string): () => void {
  let sub = subscriptions.get(runId);
  if (sub) {
    sub.refs += 1;
  } else {
    const controller = new AbortController();
    sub = { controller, refs: 1, done: false };
    subscriptions.set(runId, sub);
    const current = sub;
    const { setConn, flush } = useRunStore.getState();
    subscribeRun(runId, {
      signal: controller.signal,
      onEvent: (e) => enqueue(runId, e),
      onStatus: (s) => setConn(runId, s),
    })
      .catch(() => setConn(runId, "closed"))
      .finally(() => {
        current.done = true;
        queueMicrotask(() => flush(runId));
      });
  }
  const held = sub;
  return () => {
    held.refs -= 1;
    if (held.refs > 0) return;
    // finished runs keep their view; live streams stop when nobody watches
    subscriptions.delete(runId);
    if (!held.done) held.controller.abort();
  };
}

export function useRun(runId: string | null | undefined): RunView | undefined {
  useEffect(() => {
    if (!runId) return;
    const view = useRunStore.getState().runs[runId];
    if (view && isTerminal(view.status) && !view.buffered.length) return; // already complete
    return retainRun(runId);
  }, [runId]);
  return useRunStore((s) => (runId ? s.runs[runId] : undefined));
}

export function useRunConn(runId: string | null | undefined): RunConn {
  return useRunStore((s) => (runId ? (s.conn[runId] ?? "idle") : "idle"));
}
