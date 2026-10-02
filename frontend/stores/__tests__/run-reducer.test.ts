import { describe, expect, it } from "vitest";

import type { EventEnvelope } from "@/lib/events";
import {
  applyEvent,
  emptyRunView,
  flushBuffered,
  ingest,
  replay,
  stepCount,
  type RunView,
} from "@/stores/run-reducer";
import { FIXTURE_NAMES, loadFixture, shuffled } from "@/tests/fixtures";

/** A readable projection of RunView for snapshots (full text/preview bodies left out). */
function summarize(view: RunView) {
  return {
    status: view.status,
    seq: view.seq,
    workflow: view.workflow,
    items: view.items.map((it) => {
      switch (it.kind) {
        case "node":
          return `node ${it.node} ${it.status}${it.summary ? ` · ${it.summary}` : ""}`;
        case "reasoning":
          return `reasoning ${it.node} ${it.text.length} chars`;
        case "batch":
          return `batch ${it.callIds.map((id) => {
            const c = view.calls[id];
            return c ? `${c.tool}:${c.status}${c.children.length ? `[${c.children.map((k) => view.calls[k]?.tool).join(",")}]` : ""}` : id;
          }).join(" | ")}`;
        case "compaction":
          return `compaction ${it.phase} ${it.preTokens}→${it.postTokens ?? "?"}`;
        case "microcompact":
          return `microcompact cleared ${it.cleared} saved ${it.tokensSaved}`;
        case "warning":
          return `warning ${it.code}`;
        case "guard":
          return `guard ${it.guardKind}`;
      }
    }),
    answer: view.answer && {
      chars: view.answer.markdown.length,
      citations: view.answer.citations.length,
      unverified: view.answer.unverified,
    },
    usage: { ...view.usage, cost: Number(view.usage.cost.toFixed(6)) },
    context: {
      used: view.context.used,
      window: view.context.window,
      percent: view.context.percent,
      compactions: view.context.compactions.map((c) => `${c.kind} ${c.preTokens ?? ""}→${c.postTokens ?? ""}`),
    },
    models: view.models,
    steps: stepCount(view),
  };
}

describe.each(FIXTURE_NAMES)("fixture %s", (name) => {
  const events = loadFixture(name);
  const runId = events[0]!.run_id;
  const view = replay(runId, events);

  it("reduces to a stable RunView", () => {
    expect(summarize(view)).toMatchSnapshot();
  });

  it("ends with the run's final status and every stored event kept", () => {
    expect(view.status).not.toMatch(/pending|running/);
    expect(view.events).toHaveLength(events.length);
    expect(view.buffered).toEqual([]);
  });

  it("is idempotent under duplicated events", () => {
    const doubled = events.flatMap((e) => [e, e]);
    expect(replay(runId, doubled)).toEqual(view);
  });

  it("gives the same view for a shuffled stream", () => {
    for (const seed of [1, 7, 99]) {
      expect(replay(runId, shuffled(events, seed))).toEqual(view);
    }
  });

  it("ignores stored events at or below the current seq", () => {
    const half = events.slice(0, Math.floor(events.length / 2));
    const partial = half.reduce(applyEvent, emptyRunView(runId));
    expect(events.reduce(applyEvent, partial)).toEqual(events.reduce(applyEvent, emptyRunView(runId)));
  });
});

describe("tree and process details", () => {
  it("groups a concurrent batch and nests the subcall under its parent", () => {
    const view = replay("x", []);
    expect(view.items).toEqual([]);
    const events = loadFixture("concurrent-subcall");
    const v = replay(events[0]!.run_id, events);
    const batch = v.items.find((it) => it.kind === "batch" && it.callIds.length === 3);
    expect(batch).toBeDefined();
    const parent = v.calls["call_A1"]!;
    expect(parent.children).toHaveLength(1);
    const child = v.calls[parent.children[0]!]!;
    expect(child.parentCallId).toBe("call_A1");
    expect(v.rootCallIds).not.toContain(child.callId);
    expect(v.calls["call_A4"]!.status).toBe("error");
    expect(v.calls["call_A4"]!.error?.message).toContain("boom");
  });

  it("keeps compaction start/end as one item and records the guard", () => {
    const events = loadFixture("compaction-guard");
    const v = replay(events[0]!.run_id, events);
    const compactions = v.items.filter((it) => it.kind === "compaction");
    expect(compactions).toHaveLength(1);
    expect(compactions[0]).toMatchObject({ phase: "end" });
    expect(v.limits[0]?.kind).toBe("similar_query");
    // the blocked call shows once, as its own tool row — no extra "blocked" row
    expect(v.calls["call_B8"]?.status).toBe("limited");
    expect(v.items.some((it) => it.kind === "guard")).toBe(false);
    expect(v.context.compactions.map((c) => c.kind)).toEqual(["full"]);
  });

  it("reports a cancelled run with its graceful answer", () => {
    const events = loadFixture("cancel");
    const v = replay(events[0]!.run_id, events);
    expect(v.status).toBe("cancelled");
    expect(v.answer?.markdown).toContain("หยุดแล้ว");
  });

  it("sums usage and tracks the model per tier", () => {
    const events = loadFixture("normal");
    const v = replay(events[0]!.run_id, events);
    expect(v.usage.toolCalls).toBe(7);
    expect(v.usage.in).toBeGreaterThan(0);
    expect(v.models.quick).toContain("gemini");
    expect(v.usage.durationMs).toBeGreaterThan(0);
    expect(v.answer?.citations.length).toBe(8);
  });
});

describe("ephemeral deltas", () => {
  const base = (seq: number, extra: Partial<EventEnvelope>): EventEnvelope =>
    ({
      seq,
      run_id: "r",
      session_id: null,
      ts: "2026-09-26T00:00:00Z",
      node: "retrieve_analyze",
      call_id: null,
      parent_call_id: null,
      ephemeral: false,
      ...extra,
    }) as EventEnvelope;

  it("shows live chunks as pending and replaces them with the stored chunk", () => {
    let v = ingest(emptyRunView("r"), base(1, { type: "run.started", payload: { workflow: "research", input: {} } }));
    v = ingest(v, base(1, { type: "llm.delta", payload: { channel: "reasoning", text: "Hel" }, ephemeral: true }));
    v = ingest(v, base(1, { type: "llm.delta", payload: { channel: "reasoning", text: "lo" }, ephemeral: true }));
    expect(v.items.at(-1)).toMatchObject({ kind: "reasoning", text: "", pending: "Hello" });
    v = ingest(v, base(2, { type: "llm.delta", payload: { channel: "reasoning", text: "Hello" } }));
    expect(v.items.at(-1)).toMatchObject({ kind: "reasoning", text: "Hello", pending: "" });
    expect(v.seq).toBe(2);
  });

  it("streams the answer draft until answer.final arrives", () => {
    let v = ingest(emptyRunView("r"), base(1, { type: "run.started", payload: { workflow: "research", input: {} } }));
    v = ingest(v, base(1, { type: "llm.delta", payload: { channel: "answer", text: "PTT" }, ephemeral: true }));
    expect(v.answerDraft.pending).toBe("PTT");
    v = ingest(v, base(2, { type: "answer.final", payload: { markdown: "PTT 34.5 [1]", citations: [], unverified_numbers: [] } }));
    expect(v.answer?.markdown).toBe("PTT 34.5 [1]");
    expect(v.answerDraft).toEqual({ text: "", pending: "" });
  });

  it("buffers a gap and flushes it on demand", () => {
    let v = ingest(emptyRunView("r"), base(1, { type: "run.started", payload: { workflow: "research", input: {} } }));
    v = ingest(v, base(3, { type: "warning", payload: { code: "x", message: "late" } }));
    expect(v.seq).toBe(1);
    expect(v.buffered).toHaveLength(1);
    v = flushBuffered(v);
    expect(v.seq).toBe(3);
    expect(v.warnings).toHaveLength(1);
  });
});
