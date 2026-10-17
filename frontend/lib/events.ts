/**
 * SSE event envelope + payloads (contracts.md §10). Must match
 * backend/app/harness/events/schema.py — lib/__tests__/events.test.ts checks
 * fixture streams against backend/schemas/events.schema.json.
 */

export type RunStatus = "completed" | "failed" | "cancelled" | "interrupted" | "limited";

export type ToolStatus =
  | "ok"
  | "partial"
  | "unavailable"
  | "error"
  | "denied"
  | "limited"
  | "timeout"
  | "cancelled";

export type LLMPurpose = "agent" | "final" | "compact" | "structured" | "debate" | (string & {});

export type GuardKind = "iterations" | "tool_calls" | "similar_query" | "timeout" | "budget";

export interface Citation {
  marker: number;
  provenance_id: string;
}

/** (P17) research §B.4 */
export type DiscoveryStopReason =
  | "root_goal"
  | "max_whys"
  | "resistance"
  | "urgency"
  | "informational"
  | "memory_already_known"
  | (string & {});
export type AdviceMode = "normal" | "stabilise" | "educate";

export interface AnswerDiscovery {
  whys_asked: number;
  max: number;
  question: string;
}

export type MemoryOp = "NOOP" | "ADD" | "UPDATE" | "SUPERSEDE" | "RETIRE";

export interface MemoryChangeEvent {
  op: MemoryOp;
  id: string;
  category: string;
  text: string;
  previous_text?: string | null;
  status: string;
}

export interface EventPayloads {
  "run.started": { workflow: string; input: Record<string, unknown>; graph?: Record<string, unknown> | null };
  "run.finished": { status: RunStatus; error?: string | null; reason?: string | null };
  "node.started": { node: string; label: string; phase: string };
  "node.finished": { node: string; status: "done" | "error" | "skipped"; duration_ms: number; summary?: string | null };
  "llm.started": { provider: string; model: string; tier: "deep" | "quick"; purpose: LLMPurpose };
  "llm.delta": { channel: "reasoning" | "answer"; text: string };
  "llm.finished": {
    input_tokens: number;
    output_tokens: number;
    cost_usd: number;
    duration_ms: number;
    cache_read_tokens?: number;
    structured_mode?: "native" | "fallback_parsed" | "failed" | null;
  };
  "tool.started": {
    tool: string;
    category: string;
    args: Record<string, unknown>;
    read_only: boolean;
    batch_id: string;
  };
  "tool.progress": { message: string };
  "tool.finished": {
    call_id: string;
    tool: string;
    ok: boolean;
    status: ToolStatus;
    summary: string;
    preview: string;
    truncated: boolean;
    error?: { type: string; message: string; retryable: boolean } | null;
    provenance_ids: string[];
    duration_ms: number;
  };
  "context.usage": { used_tokens: number; context_window: number; percent: number };
  "context.microcompact": { cleared: number; tokens_saved: number };
  "context.compaction": {
    phase: "start" | "end" | "failed" | "fallback_clear";
    pre_tokens: number;
    post_tokens?: number | null;
    summary?: string | null;
    cleared?: number | null;
    kept?: number | null;
  };
  "guard.limit": { kind: GuardKind; detail: string };
  warning: { code: string; message: string };
  "approval.required": {
    approval_id: string;
    kind: string;
    title: string;
    proposal: Record<string, unknown>;
    policy_results: Record<string, unknown>[];
    provenance_ids: string[];
  };
  "approval.resolved": {
    approval_id: string;
    outcome: "approved" | "rejected" | "edited";
    edits?: Record<string, unknown> | null;
  };
  "answer.final": {
    markdown: string;
    citations: Citation[];
    unverified_numbers: string[];
    /** (P17) the answer ends with a 5 Whys question: progress n/max + the question asked */
    discovery?: AnswerDiscovery | null;
  };
  "checkpoint.saved": { node: string; thread_id: string };
  /** (P17 §17.2) what memory was recalled for this run (before any llm.*) */
  "memory.recalled": {
    items: { id: string; category: string; text: string; status?: string }[];
    reasoning: { id: string; title: string }[];
  };
  /** (P17) 5 Whys assessment of this message */
  "discovery.assessed": {
    whys_asked: number;
    root_goal_identified: boolean;
    confidence: number;
    stop_reason?: DiscoveryStopReason | null;
    advice_mode?: AdviceMode;
    next_question?: string | null;
    /** latest known goal (also before it is identified) — the chip shows it only once identified */
    root_goal?: { text: string; category?: string | null } | null;
  };
  /** (P17) what the `learn` node changed in the user's memory (after answer.final) */
  "memory.updated": { changes: MemoryChangeEvent[] };
  /** (P17) lessons distilled into the ReasoningBank */
  "reasoning.distilled": { items: { id: string; title: string; kind: string }[] };
  /** (P15) */
  "backtest.progress": {
    phase: "preload" | "decisions" | "simulate" | "done";
    total: number;
    done: number;
    cached: number;
    failed: number;
    running: number;
    cost_usd: number;
    message?: string | null;
  };
  "backtest.cell": {
    cell_id: string;
    symbol: string;
    day: string;
    status: "pending" | "running" | "done" | "failed";
    run_id?: string | null;
    rating?: string | null;
    side?: string | null;
    outcome?: string | null;
    cached: boolean;
    error?: string | null;
  };
  "backtest.sim": { sim_id: string; final_equity: string; total_return: number; trades: number };
}

export type EventType = keyof EventPayloads;

export const EVENT_TYPES = [
  "run.started",
  "run.finished",
  "node.started",
  "node.finished",
  "llm.started",
  "llm.delta",
  "llm.finished",
  "tool.started",
  "tool.progress",
  "tool.finished",
  "context.usage",
  "context.microcompact",
  "context.compaction",
  "guard.limit",
  "warning",
  "approval.required",
  "approval.resolved",
  "answer.final",
  "checkpoint.saved",
  "backtest.progress",
  "backtest.cell",
  "backtest.sim",
  "memory.recalled",
  "discovery.assessed",
  "memory.updated",
  "reasoning.distilled",
] as const satisfies readonly EventType[];


export type EventEnvelope<T extends EventType = EventType> = {
  [K in T]: {
    seq: number;
    run_id: string;
    session_id: string | null;
    ts: string;
    type: K;
    node: string | null;
    call_id: string | null;
    parent_call_id: string | null;
    payload: EventPayloads[K];
    /** live-only llm.delta chunk: not stored, superseded by the next stored llm.delta */
    ephemeral: boolean;
  };
}[T];

/** Replayed rows come from SQLite without a timezone; the backend always writes UTC. */
export function parseTs(ts: string): number {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(ts);
  return Date.parse(hasZone ? ts : `${ts}Z`);
}

export function isEventType(value: string): value is EventType {
  return (EVENT_TYPES as readonly string[]).includes(value);
}
