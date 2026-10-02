import { describe, expect, it } from "vitest";

import { takenEdges } from "@/lib/trade";
import { replay } from "@/stores/run-reducer";
import { loadTradingDetail, loadTradingRun, shuffled } from "@/tests/fixtures";

/** Trading runs recorded from the backend with FakeLLM (phase-11 §11.4). */
describe("trading run PTT (2 debate rounds, edited approval)", () => {
  const events = loadTradingRun("ptt");
  const runId = events[0]!.run_id;
  const view = replay(runId, events);

  it("finishes with every node done and nothing left running", () => {
    expect(view.status).toBe("completed");
    const statuses = Object.values(view.nodes).map((n) => n.status);
    expect(statuses.every((s) => s === "done")).toBe(true);
    expect(view.nodes.report?.status).toBe("done");
  });

  it("runs the four analysts as concurrent sub-nodes", () => {
    const subs = ["analyst.market", "analyst.news", "analyst.fundamentals", "analyst.sentiment"];
    expect(subs.every((s) => view.nodes[s]?.status === "done")).toBe(true);
    // replayed up to the last analyst start, all four are live at once
    const lastStart = Math.max(
      ...events.filter((e) => e.type === "node.started" && subs.includes(e.payload.node)).map((e) => e.seq),
    );
    const mid = replay(runId, events.filter((e) => e.seq <= lastStart));
    expect(subs.map((s) => mid.nodes[s]?.status)).toEqual(["running", "running", "running", "running"]);
    expect(mid.nodes.analysts?.status).toBe("running");
  });

  it("keeps the debate order and counts rounds", () => {
    const debate = view.path.filter((n) => n === "bull_researcher" || n === "bear_researcher");
    expect(debate).toEqual(["bull_researcher", "bear_researcher", "bull_researcher", "bear_researcher"]);
    expect(view.nodes.bull_researcher?.runs).toBe(2);
    expect(view.path.filter((n) => ["aggressive", "conservative", "neutral"].includes(n))).toEqual([
      "aggressive",
      "conservative",
      "neutral",
    ]);
  });

  it("marks checkpoints and the edges actually taken", () => {
    expect(view.nodes.trader?.checkpointed).toBe(true);
    const taken = takenEdges(view.path);
    expect(taken.has("load_context->analyst.market")).toBe(true);
    expect(taken.has("analyst.sentiment->bull_researcher")).toBe(true);
    expect(taken.has("bull_researcher->bear_researcher")).toBe(true);
    expect(taken.has("bear_researcher->bull_researcher")).toBe(true);
    expect(taken.has("policy_gate->refresh_price")).toBe(true);
    expect(taken.has("refresh_price->approval")).toBe(true);
    expect(taken.has("policy_gate->trader")).toBe(false);
  });

  it("shows the approval node waiting until it is answered", () => {
    const required = events.find((e) => e.type === "approval.required")!;
    const paused = replay(runId, events.filter((e) => e.seq <= required.seq));
    expect(paused.nodes.approval?.status).toBe("waiting");
    expect(Object.values(paused.approvals)[0]?.outcome).toBe("pending");
    expect(view.nodes.approval?.runs).toBe(2);
    expect(Object.values(view.approvals)[0]?.outcome).toBe("edited");
  });

  it("tags tool calls with their node (compute sizing in trader / PM / approval edit)", () => {
    const byNode = Object.values(view.calls).map((c) => `${c.node}:${c.tool}`);
    expect(byNode).toContain("load_context:trade_snapshot");
    expect(byNode).toContain("trader:size_trade");
    expect(byNode).toContain("approval:size_trade");
  });

  it("is order-independent (replay of a shuffled stream)", () => {
    expect(replay(runId, shuffled(events, 3))).toEqual(view);
  });

  it("detail: the decision numbers are the compute results", () => {
    const detail = loadTradingDetail("ptt");
    expect(detail.decision?.sizing.stop).toBeCloseTo(33.3);
    expect(detail.decision?.rating).toBe("Buy");
    expect(detail.invest_debate?.history.map((h) => h.speaker)).toEqual(["bull", "bear", "bull", "bear"]);
  });
});

describe("trading run EURUSD (paused at approval)", () => {
  const events = loadTradingRun("eurusd");
  const view = replay(events[0]!.run_id, events);

  it("uses macro instead of fundamentals and waits for approval", () => {
    expect(view.status).toBe("running");
    expect(view.nodes["analyst.macro"]?.status).toBe("done");
    expect(view.nodes["analyst.fundamentals"]).toBeUndefined();
    expect(view.nodes.approval?.status).toBe("waiting");
  });
});
