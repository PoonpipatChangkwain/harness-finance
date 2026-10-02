/**
 * subscribeRun: follow one run's SSE stream (GET /runs/{id}/events) with replay.
 *
 * Every request carries `Last-Event-ID` (the last stored seq seen, "0" at
 * first), so a reconnect resumes exactly where the stream stopped. Reconnects
 * back off 1s → 2s → 4s … capped at 10s. The stream ends for good after
 * `run.finished` (the backend closes it then) or when `signal` aborts.
 */

import { fetchEventSource, type EventSourceMessage } from "@microsoft/fetch-event-source";

import { API_BASE } from "./api";
import type { EventEnvelope } from "./events";

export type ConnStatus = "connecting" | "open" | "reconnecting" | "closed";

export interface SubscribeOptions {
  afterSeq?: number;
  onEvent: (e: EventEnvelope) => void;
  onStatus?: (s: ConnStatus) => void;
  signal: AbortSignal;
  baseUrl?: string;
  fetch?: typeof fetch;
  minBackoffMs?: number;
  maxBackoffMs?: number;
}

// fetch-event-source keeps this exact (lowercase) key up to date from `id:` lines
const LAST_EVENT_ID = "last-event-id";

class StreamDone extends Error {}
class FatalStreamError extends Error {}

export function backoffMs(attempt: number, min = 1000, max = 10_000): number {
  return Math.min(max, min * 2 ** Math.max(0, attempt - 1));
}

export async function subscribeRun(runId: string, opts: SubscribeOptions): Promise<void> {
  const { onEvent, onStatus, signal } = opts;
  const afterSeq = opts.afterSeq ?? 0;
  let attempt = 0;
  let finished = false;
  onStatus?.("connecting");
  try {
    await fetchEventSource(`${opts.baseUrl ?? API_BASE}/runs/${runId}/events`, {
      signal,
      fetch: opts.fetch,
      openWhenHidden: true,
      headers: { [LAST_EVENT_ID]: String(afterSeq) },
      async onopen(res) {
        if (res.ok && (res.headers.get("content-type") ?? "").includes("text/event-stream")) {
          attempt = 0;
          onStatus?.("open");
          return;
        }
        if (res.status === 404) throw new FatalStreamError(`run ${runId} not found`);
        throw new Error(`stream open failed: ${res.status}`);
      },
      onmessage(msg: EventSourceMessage) {
        if (!msg.data) return; // keep-alive ping
        const e = JSON.parse(msg.data) as EventEnvelope;
        onEvent(e);
        if (e.type === "run.finished") finished = true;
      },
      onclose() {
        // the backend closes after run.finished; any other close is a dropped connection
        if (finished) throw new StreamDone();
        throw new Error("stream closed early");
      },
      onerror(err: unknown) {
        if (err instanceof StreamDone || err instanceof FatalStreamError) throw err;
        attempt += 1;
        onStatus?.("reconnecting");
        return backoffMs(attempt, opts.minBackoffMs, opts.maxBackoffMs);
      },
    });
  } catch (err) {
    if (!(err instanceof StreamDone)) throw err;
  } finally {
    onStatus?.("closed");
  }
}
