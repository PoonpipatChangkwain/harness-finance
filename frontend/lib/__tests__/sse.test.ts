import { describe, expect, it } from "vitest";

import type { EventEnvelope } from "@/lib/events";
import { backoffMs, subscribeRun, type ConnStatus } from "@/lib/sse";

function sseBody(events: Partial<EventEnvelope>[], { close }: { close: "end" | "error" }): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  const chunks = events.map((e) => {
    const id = e.ephemeral ? "" : `id: ${e.seq}\n`;
    return enc.encode(`event: ${e.type}\n${id}data: ${JSON.stringify(e)}\n\n`);
  });
  // pull-based so every chunk is read before the stream ends (controller.error drops queued chunks)
  return new ReadableStream({
    pull(controller) {
      const chunk = chunks.shift();
      if (chunk) controller.enqueue(chunk);
      else if (close === "error") controller.error(new Error("connection reset"));
      else controller.close();
    },
  });
}

const ev = (seq: number, type: string, extra: Partial<EventEnvelope> = {}) =>
  ({ seq, run_id: "run_1", type, payload: {}, ephemeral: false, ...extra }) as Partial<EventEnvelope>;

function okResponse(body: ReadableStream<Uint8Array>): Response {
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
}

describe("subscribeRun", () => {
  it("reconnects after a dropped stream with the last stored seq as Last-Event-ID", async () => {
    const seen: string[] = [];
    const requests: (string | null)[] = [];
    const statuses: ConnStatus[] = [];
    const responses = [
      // first connection drops after seq 2 (plus a live-only chunk that has no id)
      sseBody([ev(1, "run.started"), ev(2, "tool.started"), ev(2, "llm.delta", { ephemeral: true })], { close: "error" }),
      sseBody([ev(3, "tool.finished"), ev(4, "run.finished")], { close: "end" }),
    ];
    const fetchMock = (async (_url: string, init?: RequestInit) => {
      requests.push(new Headers(init?.headers).get("last-event-id"));
      return okResponse(responses.shift()!);
    }) as typeof fetch;

    await subscribeRun("run_1", {
      signal: new AbortController().signal,
      fetch: fetchMock,
      baseUrl: "http://test/api",
      minBackoffMs: 1,
      maxBackoffMs: 5,
      onEvent: (e) => seen.push(`${e.seq}:${e.type}`),
      onStatus: (s) => statuses.push(s),
    });

    expect(requests).toEqual(["0", "2"]);
    expect(seen).toEqual(["1:run.started", "2:tool.started", "2:llm.delta", "3:tool.finished", "4:run.finished"]);
    expect(statuses).toEqual(["connecting", "open", "reconnecting", "open", "closed"]);
  });

  it("stops after run.finished without reconnecting", async () => {
    let calls = 0;
    const fetchMock = (async () => {
      calls += 1;
      return okResponse(sseBody([ev(1, "run.started"), ev(2, "run.finished")], { close: "end" }));
    }) as typeof fetch;
    await subscribeRun("run_1", {
      signal: new AbortController().signal,
      fetch: fetchMock,
      baseUrl: "http://test/api",
      afterSeq: 0,
      onEvent: () => {},
    });
    expect(calls).toBe(1);
  });

  it("gives up on 404", async () => {
    const fetchMock = (async () => new Response("{}", { status: 404 })) as typeof fetch;
    await expect(
      subscribeRun("run_1", { signal: new AbortController().signal, fetch: fetchMock, baseUrl: "http://t", onEvent: () => {} }),
    ).rejects.toThrow(/not found/);
  });

  it("backs off 1s → 10s", () => {
    expect([1, 2, 3, 4, 5, 9].map((n) => backoffMs(n))).toEqual([1000, 2000, 4000, 8000, 10000, 10000]);
  });
});
