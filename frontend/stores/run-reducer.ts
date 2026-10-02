/**
 * Pure reducer: SSE events → RunView (ui-spec §8, phase-08 §8.1).
 *
 * Behaviour modelled on deepseek-harness `ui-chat/.../use-turn-data.ts` and
 * `step-process.ts` (process items in seq order, tool tree from
 * call_id/parent_call_id). One research run = one turn of the conversation.
 *
 * - `applyEvent` is idempotent: a stored event with `seq <= view.seq` is ignored.
 * - `ingest` also tolerates out-of-order delivery: events after a gap wait in
 *   `buffered` until the gap is filled (`flushBuffered` applies them anyway).
 * - Ephemeral `llm.delta` chunks go to `pending` text and are replaced by the
 *   next stored `llm.delta` of the same channel (contracts §10 coalescing).
 */

import { parseTs, type EventEnvelope, type EventPayloads, type GuardKind, type ToolStatus } from "@/lib/events";

export type RunViewStatus = "pending" | "running" | EventPayloads["run.finished"]["status"];

export interface CallView {
  callId: string;
  tool: string;
  category: string;
  args: Record<string, unknown>;
  readOnly: boolean;
  batchId: string | null;
  node: string | null;
  parentCallId: string | null;
  seq: number;
  startedAt: number;
  finishedAt: number | null;
  status: "running" | ToolStatus;
  progress: string | null;
  summary: string | null;
  preview: string | null;
  truncated: boolean;
  error: EventPayloads["tool.finished"]["error"] | null;
  provenanceIds: string[];
  durationMs: number | null;
  children: string[];
}

export type ProcessItem =
  | {
      kind: "node";
      seq: number;
      node: string;
      label: string;
      phase: string;
      status: "running" | "done" | "error" | "skipped";
      durationMs: number | null;
      summary: string | null;
    }
  | { kind: "reasoning"; seq: number; node: string | null; text: string; pending: string }
  | { kind: "batch"; seq: number; node: string | null; batchId: string; callIds: string[] }
  | {
      kind: "compaction";
      seq: number;
      phase: EventPayloads["context.compaction"]["phase"];
      preTokens: number;
      postTokens: number | null;
      summary: string | null;
      cleared: number | null;
      kept: number | null;
    }
  | { kind: "microcompact"; seq: number; cleared: number; tokensSaved: number }
  | { kind: "warning"; seq: number; node: string | null; code: string; message: string }
  | { kind: "guard"; seq: number; node: string | null; guardKind: GuardKind; detail: string; callId: string | null };

export interface LlmCallView {
  seq: number;
  node: string | null;
  provider: string;
  model: string;
  tier: "deep" | "quick";
  purpose: string;
  startedAt: number;
  finishedAt: number | null;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  costUsd: number;
  durationMs: number | null;
  /** an attempt that never finished: a newer llm.started of the same node superseded it (failover) */
  failed: boolean;
}

export interface UsageView {
  in: number;
  out: number;
  cacheRead: number;
  cost: number;
  llmCalls: number;
  toolCalls: number;
  durationMs: number | null;
  byPurpose: Record<string, { calls: number; in: number; out: number; cost: number }>;
}

export interface CompactionView {
  seq: number;
  kind: "full" | "failed" | "fallback_clear" | "micro";
  preTokens: number | null;
  postTokens: number | null;
  summary: string | null;
  cleared: number | null;
  kept: number | null;
  tokensSaved: number | null;
  ts: number;
}

export interface ApprovalView {
  approvalId: string;
  kind: string;
  title: string;
  proposal: Record<string, unknown>;
  policyResults: Record<string, unknown>[];
  provenanceIds: string[];
  outcome: "pending" | "approved" | "rejected" | "edited";
}

/** Live state of one graph node (P11 PipelineGraph): the latest of its runs, counted. */
export interface NodeRunView {
  node: string;
  label: string;
  phase: string;
  status: "running" | "done" | "error" | "skipped" | "waiting";
  /** how many times the node started (debate nodes run once per round) */
  runs: number;
  startedAt: number | null;
  durationMs: number | null;
  summary: string | null;
  /** a checkpoint was saved after this node (resume can start after it) */
  checkpointed: boolean;
}

export interface RunView {
  runId: string;
  sessionId: string | null;
  status: RunViewStatus;
  seq: number;
  workflow: string | null;
  input: Record<string, unknown>;
  startedAt: number | null;
  finishedAt: number | null;
  error: string | null;
  reason: string | null;
  items: ProcessItem[];
  calls: Record<string, CallView>;
  rootCallIds: string[];
  llm: LlmCallView[];
  models: { deep: string | null; quick: string | null };
  answerDraft: { text: string; pending: string };
  answer: { markdown: string; citations: EventPayloads["answer.final"]["citations"]; unverified: string[] } | null;
  context: { used: number; window: number; percent: number; compactions: CompactionView[] };
  warnings: { seq: number; code: string; message: string }[];
  limits: { seq: number; kind: GuardKind; detail: string }[];
  approvals: Record<string, ApprovalView>;
  /** graph workflows: per-node state, and every node.started in order (edges actually taken) */
  nodes: Record<string, NodeRunView>;
  path: string[];
  usage: UsageView;
  /** stored events in seq order (Trajectory tab, scratchpad download) */
  events: EventEnvelope[];
  /** out-of-order events waiting for the gap before them */
  buffered: EventEnvelope[];
}

export function emptyRunView(runId: string): RunView {
  return {
    runId,
    sessionId: null,
    status: "pending",
    seq: 0,
    workflow: null,
    input: {},
    startedAt: null,
    finishedAt: null,
    error: null,
    reason: null,
    items: [],
    calls: {},
    rootCallIds: [],
    llm: [],
    models: { deep: null, quick: null },
    answerDraft: { text: "", pending: "" },
    answer: null,
    context: { used: 0, window: 0, percent: 0, compactions: [] },
    warnings: [],
    limits: [],
    approvals: {},
    nodes: {},
    path: [],
    usage: { in: 0, out: 0, cacheRead: 0, cost: 0, llmCalls: 0, toolCalls: 0, durationMs: null, byPurpose: {} },
    events: [],
    buffered: [],
  };
}

export const isTerminal = (s: RunViewStatus) => s !== "pending" && s !== "running";

function lastItem(view: RunView): ProcessItem | undefined {
  return view.items[view.items.length - 1];
}

function withItem(view: RunView, item: ProcessItem): RunView {
  return { ...view, items: [...view.items, item] };
}

function replaceItem(view: RunView, index: number, item: ProcessItem): RunView {
  const items = view.items.slice();
  items[index] = item;
  return { ...view, items };
}

/** LLM calls still open when their node / the run ends never finished: mark them failed. */
function closeOpenLlm(llm: LlmCallView[], ts: number, node?: string | null): LlmCallView[] {
  return llm.map((c) => (c.finishedAt === null && (node === undefined || c.node === node) ? { ...c, finishedAt: ts, failed: true } : c));
}

function applyDelta(view: RunView, e: EventEnvelope<"llm.delta">): RunView {
  const { channel, text } = e.payload;
  if (channel === "answer") {
    const d = view.answerDraft;
    return {
      ...view,
      answerDraft: e.ephemeral ? { text: d.text, pending: d.pending + text } : { text: d.text + text, pending: "" },
    };
  }
  const last = lastItem(view);
  if (last && last.kind === "reasoning" && last.node === e.node) {
    const next = e.ephemeral
      ? { ...last, pending: last.pending + text }
      : { ...last, text: last.text + text, pending: "" };
    return replaceItem(view, view.items.length - 1, next);
  }
  return withItem(view, {
    kind: "reasoning",
    seq: e.seq,
    node: e.node,
    text: e.ephemeral ? "" : text,
    pending: e.ephemeral ? text : "",
  });
}

function newCall(e: EventEnvelope, tool: string): CallView {
  return {
    callId: e.call_id ?? `seq_${e.seq}`,
    tool,
    category: "data",
    args: {},
    readOnly: true,
    batchId: null,
    node: e.node,
    parentCallId: e.parent_call_id,
    seq: e.seq,
    startedAt: parseTs(e.ts),
    finishedAt: null,
    status: "running",
    progress: null,
    summary: null,
    preview: null,
    truncated: false,
    error: null,
    provenanceIds: [],
    durationMs: null,
    children: [],
  };
}

/** Put a new call into the tree: under its parent, or into a root batch item. */
function attachCall(view: RunView, call: CallView, e: EventEnvelope): RunView {
  const calls = { ...view.calls, [call.callId]: call };
  const parent = call.parentCallId ? view.calls[call.parentCallId] : undefined;
  if (parent) {
    calls[parent.callId] = { ...parent, children: [...parent.children, call.callId] };
    return { ...view, calls };
  }
  const rootCallIds = [...view.rootCallIds, call.callId];
  const batchId = call.batchId ?? `solo_${call.callId}`;
  const index = view.items.findIndex((it) => it.kind === "batch" && it.batchId === batchId);
  if (index >= 0) {
    const it = view.items[index] as Extract<ProcessItem, { kind: "batch" }>;
    return replaceItem({ ...view, calls, rootCallIds }, index, { ...it, callIds: [...it.callIds, call.callId] });
  }
  return withItem({ ...view, calls, rootCallIds }, { kind: "batch", seq: e.seq, node: e.node, batchId, callIds: [call.callId] });
}

function addUsage(usage: UsageView, purpose: string, p: EventPayloads["llm.finished"]): UsageView {
  const prev = usage.byPurpose[purpose] ?? { calls: 0, in: 0, out: 0, cost: 0 };
  // counted on finish, so abandoned failover attempts are not "LLM calls"
  return {
    ...usage,
    llmCalls: usage.llmCalls + 1,
    in: usage.in + p.input_tokens,
    out: usage.out + p.output_tokens,
    cacheRead: usage.cacheRead + (p.cache_read_tokens ?? 0),
    cost: usage.cost + p.cost_usd,
    byPurpose: {
      ...usage.byPurpose,
      [purpose]: {
        calls: prev.calls + 1,
        in: prev.in + p.input_tokens,
        out: prev.out + p.output_tokens,
        cost: prev.cost + p.cost_usd,
      },
    },
  };
}

function patchNode(view: RunView, node: string, patch: Partial<NodeRunView>): RunView {
  const prev = view.nodes[node];
  if (!prev) return view;
  return { ...view, nodes: { ...view.nodes, [node]: { ...prev, ...patch } } };
}

/** Node-level bookkeeping shared by every graph event; the process items are built in `reduce`. */
function reduceNodes(view: RunView, e: EventEnvelope, ts: number): RunView {
  switch (e.type) {
    case "node.started": {
      const p = e.payload;
      const prev = view.nodes[p.node];
      const node: NodeRunView = {
        node: p.node,
        label: p.label,
        phase: p.phase,
        status: "running",
        runs: (prev?.runs ?? 0) + 1,
        startedAt: ts,
        durationMs: null,
        summary: prev?.summary ?? null,
        checkpointed: prev?.checkpointed ?? false,
      };
      return { ...view, nodes: { ...view.nodes, [p.node]: node }, path: [...view.path, p.node] };
    }
    case "node.finished":
      return patchNode(view, e.payload.node, {
        status: e.payload.status,
        durationMs: e.payload.duration_ms,
        summary: e.payload.summary ?? null,
      });
    case "checkpoint.saved":
      return patchNode(view, e.payload.node, { checkpointed: true });
    case "approval.required":
      return patchNode(view, e.node ?? "approval", { status: "waiting" });
    case "approval.resolved": {
      const waiting = Object.values(view.nodes).find((n) => n.status === "waiting");
      return waiting ? patchNode(view, waiting.node, { status: "done" }) : view;
    }
    case "run.finished": {
      if (e.payload.status === "completed") return view;
      // nodes cut off by a failure / cancel: red on failure, amber otherwise
      const cut: NodeRunView["status"] = e.payload.status === "failed" ? "error" : "skipped";
      const nodes = Object.fromEntries(
        Object.entries(view.nodes).map(([k, n]) => [k, n.status === "running" ? { ...n, status: cut } : n]),
      );
      return { ...view, nodes };
    }
    default:
      return view;
  }
}

function reduce(view: RunView, e: EventEnvelope): RunView {
  return reduceItems(reduceNodes(view, e, parseTs(e.ts)), e);
}

function reduceItems(view: RunView, e: EventEnvelope): RunView {
  const ts = parseTs(e.ts);
  switch (e.type) {
    case "run.started":
      return {
        ...view,
        status: "running",
        sessionId: e.session_id,
        workflow: e.payload.workflow,
        input: e.payload.input,
        startedAt: ts,
      };
    case "run.finished": {
      const durationMs = view.startedAt !== null ? ts - view.startedAt : null;
      const items = view.items.map((it) =>
        it.kind === "reasoning" && it.pending ? { ...it, text: it.text + it.pending, pending: "" } : it,
      );
      return {
        ...view,
        items,
        llm: closeOpenLlm(view.llm, ts),
        status: e.payload.status,
        error: e.payload.error ?? null,
        reason: e.payload.reason ?? null,
        finishedAt: ts,
        usage: { ...view.usage, durationMs },
      };
    }
    case "node.started":
      return withItem(view, {
        kind: "node",
        seq: e.seq,
        node: e.payload.node,
        label: e.payload.label,
        phase: e.payload.phase,
        status: "running",
        durationMs: null,
        summary: null,
      });
    case "node.finished": {
      const index = view.items.findLastIndex((it) => it.kind === "node" && it.node === e.payload.node);
      if (index < 0) return view;
      const it = view.items[index] as Extract<ProcessItem, { kind: "node" }>;
      return replaceItem({ ...view, llm: closeOpenLlm(view.llm, ts, e.payload.node) }, index, {
        ...it,
        status: e.payload.status,
        durationMs: e.payload.duration_ms,
        summary: e.payload.summary ?? null,
      });
    }
    case "llm.started": {
      const p = e.payload;
      const call: LlmCallView = {
        seq: e.seq,
        node: e.node,
        provider: p.provider,
        model: p.model,
        tier: p.tier,
        purpose: p.purpose,
        startedAt: ts,
        finishedAt: null,
        inputTokens: 0,
        outputTokens: 0,
        cacheReadTokens: 0,
        costUsd: 0,
        durationMs: null,
        failed: false,
      };
      // a still-open call of this node was abandoned (retry / failover to the next provider)
      const llm = closeOpenLlm(view.llm, ts, e.node);
      return {
        ...view,
        llm: [...llm, call],
        models: { ...view.models, [p.tier]: `${p.provider}:${p.model}` },
      };
    }
    case "llm.finished": {
      const p = e.payload;
      // an LLM call that failed over emits llm.started per attempt: the finished one is the latest open call
      const index = view.llm.findLastIndex((c) => c.finishedAt === null && c.node === e.node);
      const llm = view.llm.slice();
      let purpose = "agent";
      const open = index >= 0 ? llm[index] : undefined;
      if (open) {
        purpose = open.purpose;
        llm[index] = {
          ...open,
          finishedAt: ts,
          inputTokens: p.input_tokens,
          outputTokens: p.output_tokens,
          cacheReadTokens: p.cache_read_tokens ?? 0,
          costUsd: p.cost_usd,
          durationMs: p.duration_ms,
        };
      }
      return { ...view, llm, usage: addUsage(view.usage, purpose, p) };
    }
    case "llm.delta":
      return applyDelta(view, e);
    case "tool.started": {
      const p = e.payload;
      const call: CallView = {
        ...newCall(e, p.tool),
        category: p.category,
        args: p.args,
        readOnly: p.read_only,
        batchId: p.batch_id,
      };
      const next = attachCall(view, call, e);
      return { ...next, usage: { ...next.usage, toolCalls: next.usage.toolCalls + 1 } };
    }
    case "tool.progress": {
      const call = e.call_id ? view.calls[e.call_id] : undefined;
      if (!call) return view;
      return { ...view, calls: { ...view.calls, [call.callId]: { ...call, progress: e.payload.message } } };
    }
    case "tool.finished": {
      const p = e.payload;
      let next = view;
      if (!view.calls[p.call_id]) {
        // a call refused before it started (guard) still gets a row
        next = attachCall(view, { ...newCall({ ...e, call_id: p.call_id }, p.tool), callId: p.call_id }, e);
      }
      const call = next.calls[p.call_id] as CallView;
      return {
        ...next,
        calls: {
          ...next.calls,
          [p.call_id]: {
            ...call,
            status: p.status,
            summary: p.summary,
            preview: p.preview,
            truncated: p.truncated,
            error: p.error ?? null,
            provenanceIds: p.provenance_ids,
            durationMs: p.duration_ms,
            finishedAt: ts,
            progress: null,
          },
        },
      };
    }
    case "context.usage":
      return {
        ...view,
        context: {
          ...view.context,
          used: e.payload.used_tokens,
          window: e.payload.context_window,
          percent: e.payload.percent,
        },
      };
    case "context.microcompact": {
      const c: CompactionView = {
        seq: e.seq,
        kind: "micro",
        preTokens: null,
        postTokens: null,
        summary: null,
        cleared: e.payload.cleared,
        kept: null,
        tokensSaved: e.payload.tokens_saved,
        ts,
      };
      return withItem(
        { ...view, context: { ...view.context, compactions: [...view.context.compactions, c] } },
        { kind: "microcompact", seq: e.seq, cleared: e.payload.cleared, tokensSaved: e.payload.tokens_saved },
      );
    }
    case "context.compaction": {
      const p = e.payload;
      const item: ProcessItem = {
        kind: "compaction",
        seq: e.seq,
        phase: p.phase,
        preTokens: p.pre_tokens,
        postTokens: p.post_tokens ?? null,
        summary: p.summary ?? null,
        cleared: p.cleared ?? null,
        kept: p.kept ?? null,
      };
      // "start" is replaced in place by its "end"/"failed"
      const index = view.items.findLastIndex((it) => it.kind === "compaction" && it.phase === "start");
      const base = p.phase !== "start" && p.phase !== "fallback_clear" && index >= 0 ? replaceItem(view, index, item) : withItem(view, item);
      if (p.phase === "start") return base;
      const c: CompactionView = {
        seq: e.seq,
        kind: p.phase === "end" ? "full" : p.phase,
        preTokens: p.pre_tokens,
        postTokens: p.post_tokens ?? null,
        summary: p.summary ?? null,
        cleared: p.cleared ?? null,
        kept: p.kept ?? null,
        tokensSaved: p.post_tokens != null ? p.pre_tokens - p.post_tokens : null,
        ts,
      };
      return { ...base, context: { ...base.context, compactions: [...base.context.compactions, c] } };
    }
    case "guard.limit": {
      const limited = { ...view, limits: [...view.limits, { seq: e.seq, kind: e.payload.kind, detail: e.payload.detail }] };
      // a blocked tool call already has its own row (status "limited" + this detail);
      // a second "blocked" row would show every blocked call twice
      if (e.call_id && view.calls[e.call_id]) return limited;
      return withItem(
        limited,
        { kind: "guard", seq: e.seq, node: e.node, guardKind: e.payload.kind, detail: e.payload.detail, callId: e.call_id },
      );
    }
    case "warning":
      return withItem(
        { ...view, warnings: [...view.warnings, { seq: e.seq, code: e.payload.code, message: e.payload.message }] },
        { kind: "warning", seq: e.seq, node: e.node, code: e.payload.code, message: e.payload.message },
      );
    case "approval.required": {
      const p = e.payload;
      return {
        ...view,
        approvals: {
          ...view.approvals,
          [p.approval_id]: {
            approvalId: p.approval_id,
            kind: p.kind,
            title: p.title,
            proposal: p.proposal,
            policyResults: p.policy_results,
            provenanceIds: p.provenance_ids,
            outcome: "pending",
          },
        },
      };
    }
    case "approval.resolved": {
      const a = view.approvals[e.payload.approval_id];
      if (!a) return view;
      return { ...view, approvals: { ...view.approvals, [a.approvalId]: { ...a, outcome: e.payload.outcome } } };
    }
    case "answer.final":
      return {
        ...view,
        answer: {
          markdown: e.payload.markdown,
          citations: e.payload.citations,
          unverified: e.payload.unverified_numbers,
        },
        answerDraft: { text: "", pending: "" },
      };
    case "checkpoint.saved":
      return view;
    default:
      return view;
  }
}

/** Apply one event. Stored events at or below `view.seq` are ignored (idempotent replay). */
export function applyEvent(view: RunView, e: EventEnvelope): RunView {
  if (e.run_id !== view.runId) return view;
  if (e.ephemeral) return isTerminal(view.status) ? view : reduce(view, e);
  if (e.seq <= view.seq) return view;
  const next = reduce(view, e);
  return { ...next, seq: e.seq, events: [...view.events, e] };
}

function drain(view: RunView): RunView {
  let v = view;
  for (;;) {
    const i = v.buffered.findIndex((b) => b.seq === v.seq + 1);
    if (i < 0) return v;
    const e = v.buffered[i] as EventEnvelope;
    v = applyEvent({ ...v, buffered: v.buffered.filter((_, j) => j !== i) }, e);
  }
}

/** Apply in seq order: events after a gap are buffered until the gap fills. */
export function ingest(view: RunView, e: EventEnvelope): RunView {
  if (e.ephemeral || e.run_id !== view.runId) return applyEvent(view, e);
  if (e.seq <= view.seq || view.buffered.some((b) => b.seq === e.seq)) return view;
  if (e.seq === view.seq + 1) return drain(applyEvent(view, e));
  return { ...view, buffered: [...view.buffered, e] };
}

/** Give up on a gap (e.g. the stream closed): apply whatever is buffered, in seq order. */
export function flushBuffered(view: RunView): RunView {
  const pending = [...view.buffered].sort((a, b) => a.seq - b.seq);
  return pending.reduce(applyEvent, { ...view, buffered: [] });
}

export function replay(runId: string, events: EventEnvelope[]): RunView {
  return flushBuffered(events.reduce(ingest, emptyRunView(runId)));
}

/** Process items counted as "steps" in the TurnProcess header. */
export function stepCount(view: RunView): number {
  return view.items.filter((it) => it.kind !== "node").length;
}
