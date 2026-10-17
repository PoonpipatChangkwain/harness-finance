import { describe, expect, it } from "vitest";

import type { EventEnvelope } from "@/lib/events";
import { applyEvent, emptyRunView, replay } from "@/stores/run-reducer";
import { loadDiscoveryRuns, shuffled } from "@/tests/fixtures";

/**
 * (P17) The recorded 5 Whys session (research-discovery.jsonl, backend
 * test_discovery_three_turns_ask_ask_identified): turn 1 research + why 1/5, turn 2 ask-only
 * why 2/5, turn 3 root goal identified → goal memory + a distilled lesson.
 */
const [turn1, turn2, turn3] = loadDiscoveryRuns() as [EventEnvelope[], EventEnvelope[], EventEnvelope[]];
const view1 = replay(turn1[0]!.run_id, turn1);
const view2 = replay(turn2[0]!.run_id, turn2);
const view3 = replay(turn3[0]!.run_id, turn3);

const Q1 = "ถ้าได้ถือ PTT แล้วจะช่วยอะไรคุณ?";
const Q2 = "เงินที่โตขึ้นนั้นตั้งใจจะใช้ทำอะไรครับ?";
const GOAL = "เก็บเงินดาวน์บ้านภายในปี 2028";

describe("run-reducer: P17 memory events (recorded fixture)", () => {
  it("has three completed runs", () => {
    for (const v of [view1, view2, view3]) expect(v.status).toBe("completed");
  });

  it("turn 1 collects the recalled item (with its status) and the lesson used", () => {
    expect(view1.memory.recalled).toEqual([
      expect.objectContaining({ category: "preference", text: "ผู้ใช้ชอบหุ้นปันผลอย่าง PTT", status: "active" }),
    ]);
    expect(view1.memory.reasoning.map((r) => r.title)).toEqual(["Answer with data first, then ask why"]);
    expect(view1.memory.changes).toEqual([]);
    expect(view1.memory.distilled).toEqual([]);
  });

  it("turn 1 merges discovery.assessed with answer.final.discovery (research_then_ask)", () => {
    expect(view1.memory.discovery).toMatchObject({
      whysAsked: 1,
      max: 5,
      question: Q1,
      nextQuestion: Q1,
      rootGoalIdentified: false,
      rootGoal: null,
      confidence: 0.2,
      adviceMode: "normal",
      stopReason: null,
    });
    // the answer has its citations and ends with the question
    expect(view1.answer?.citations.length).toBeGreaterThan(0);
    expect(view1.answer?.markdown.trim().endsWith(Q1)).toBe(true);
  });

  it("the v2 graph nodes show up in the run's node path", () => {
    expect(view1.path).toEqual(
      expect.arrayContaining(["plan", "recall_memory", "assess_discovery", "retrieve_analyze", "synthesize_report", "learn"]),
    );
    expect(view1.nodes.recall_memory?.label).toBe("Recall memory");
    expect(view1.nodes.assess_discovery?.label).toBe("Understand goal");
    expect(view1.nodes.learn?.label).toBe("Learn");
    // ask-only turn: no research, just the question
    expect(view2.path).toContain("write_question");
    expect(view2.path).not.toContain("retrieve_analyze");
    expect(view2.nodes.write_question?.label).toBe("Ask why");
  });

  it("turn 2 asks why 2/5 without research", () => {
    expect(view2.memory.discovery).toMatchObject({ whysAsked: 2, max: 5, question: Q2, rootGoalIdentified: false });
    expect(view2.answer?.citations).toEqual([]);
  });

  it("turn 3: root goal identified (text from the payload), no question, goal remembered, lesson distilled", () => {
    expect(view3.memory.discovery).toMatchObject({
      rootGoalIdentified: true,
      rootGoal: GOAL,
      question: null,
      nextQuestion: null,
      stopReason: "root_goal",
    });
    expect(view3.memory.changes).toEqual([
      expect.objectContaining({ op: "ADD", category: "goal", text: `ผู้ใช้${GOAL}`, status: "active" }),
    ]);
    expect(view3.memory.distilled).toEqual([
      expect.objectContaining({ title: "Tie a single-stock idea to the user's dated goal", kind: "strategy" }),
    ]);
  });

  it("is idempotent: duplicated and shuffled streams give the same view", () => {
    for (const [events, view] of [
      [turn1, view1],
      [turn3, view3],
    ] as const) {
      const id = events[0]!.run_id;
      expect(replay(id, events.flatMap((e) => [e, e]))).toEqual(view);
      for (const seed of [3, 11]) expect(replay(id, shuffled(events, seed))).toEqual(view);
    }
  });

  it("ignores a re-delivered event with a stale seq", () => {
    const updated = turn3.find((e) => e.type === "memory.updated")!;
    expect(applyEvent(view3, updated)).toBe(view3);
  });

  it("a later memory.updated for the same item replaces the row instead of duplicating it", () => {
    const updated = turn3.find((e) => e.type === "memory.updated") as EventEnvelope<"memory.updated">;
    const change = updated.payload.changes[0]!;
    const later = {
      ...updated,
      seq: view3.seq + 1,
      payload: { changes: [{ ...change, text: `${change.text} (ยืนยันแล้ว)` }] },
    } as EventEnvelope;
    const next = applyEvent(view3, later);
    expect(next.memory.changes.filter((c) => c.id === change.id)).toHaveLength(1);
    expect(next.memory.changes[0]?.text).toContain("ยืนยันแล้ว");
  });

  it("a run without memory events keeps the empty slice", () => {
    const v = emptyRunView("r");
    expect(v.memory).toEqual({ recalled: [], reasoning: [], changes: [], distilled: [], discovery: null });
  });
});
