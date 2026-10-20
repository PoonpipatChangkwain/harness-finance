"use client";

/**
 * Live pipeline of a trading run (ui-spec §5, phase-11 §11.2): the workflow graph
 * (analysts expanded per analyst) laid out left→right with dagre, each node showing
 * StateDot + label + duration + tool-call badge, 💾 once checkpointed, an amber
 * blinking border while it waits for approval, and "round n/N" on debate nodes.
 * Conditional edges are dashed with their router key; edges the run took are
 * highlighted. Status comes from `run-store` (`RunView.nodes`, from node events).
 * Run-state shape after deepseek-harness `ui-workflow-run/WorkflowRunPanel.tsx`.
 */

import dagre from "@dagrejs/dagre";
import {
  Background,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  applyNodeChanges,
  type Edge,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { clsx } from "clsx";
import { Save, Wrench } from "lucide-react";
import { memo, useCallback, useEffect, useMemo, useState } from "react";

import { StateDot, type DotState } from "@/components/ui/StateDot";
import { formatDuration } from "@/lib/format";
import { useT } from "@/lib/i18n";
import { buildPipeline, debateRound, takenEdges, type PipelineEdge, type PipelineNode } from "@/lib/trade";
import type { AnalystName, GraphView } from "@/lib/trade-api";
import type { NodeRunView, RunView } from "@/stores/run-reducer";

const NODE_W = 150;
const NODE_H = 54;

export interface PipelineNodeData extends Record<string, unknown> {
  label: string;
  kind: string;
  status: NodeRunView["status"] | "idle";
  durationMs: number | null;
  tools: number;
  checkpointed: boolean;
  round: { round: number; of: number } | null;
  selected: boolean;
}

const DOT: Record<PipelineNodeData["status"], DotState> = {
  idle: "idle",
  running: "ongoing",
  done: "done",
  error: "error",
  skipped: "warning",
  waiting: "warning",
};

/** dagre LR layout of the pipeline; positions are top-left corners for React Flow. */
export function layoutPipeline(nodes: PipelineNode[], edges: PipelineEdge[]): Record<string, { x: number; y: number }> {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", nodesep: 18, ranksep: 46, marginx: 8, marginy: 8 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of nodes) g.setNode(n.id, { width: NODE_W, height: NODE_H });
  // edges back into the report are drawn but do not pull the layout (keeps the main line straight)
  for (const e of edges) if (e.target !== "report" || e.source === "record_decision") g.setEdge(e.source, e.target);
  dagre.layout(g);
  const out: Record<string, { x: number; y: number }> = {};
  for (const n of nodes) {
    const p = g.node(n.id) as { x: number; y: number } | undefined;
    out[n.id] = { x: (p?.x ?? 0) - NODE_W / 2, y: (p?.y ?? 0) - NODE_H / 2 };
  }
  return wrapRows(out, WRAP_AT);
}

/** First node of the second row: the one-line pipeline is ~15 ranks wide, too small once fitted. */
const WRAP_AT = "aggressive";

/** Move everything from `pivot`'s column rightwards onto a second row below the first. */
export function wrapRows(
  pos: Record<string, { x: number; y: number }>,
  pivot: string,
): Record<string, { x: number; y: number }> {
  const at = pos[pivot];
  if (!at) return pos;
  const values = Object.values(pos);
  const minX = Math.min(...values.map((p) => p.x));
  const maxY = Math.max(...values.map((p) => p.y));
  const minY = Math.min(...values.map((p) => p.y));
  const out: Record<string, { x: number; y: number }> = {};
  for (const [id, p] of Object.entries(pos)) {
    out[id] = p.x >= at.x - 1 ? { x: p.x - at.x + minX, y: p.y - minY + maxY + NODE_H + 40 } : p;
  }
  return out;
}

export const PipelineNodeCard = memo(function PipelineNodeCard({ data }: NodeProps<Node<PipelineNodeData>>) {
  const t = useT();
  return (
    <div
      data-testid="pipeline-node"
      data-status={data.status}
      className={clsx(
        "flex h-[54px] w-[150px] flex-col justify-center rounded-md border bg-layer-1 px-2 text-left shadow-sm",
        data.selected ? "border-accent ring-1 ring-accent" : "border-line-strong",
        data.status === "waiting" && "fh-blink-warning border-warning",
        data.status === "error" && "border-error",
      )}
    >
      <Handle type="target" position={Position.Left} className="!size-1.5 !border-0 !bg-line-strong" />
      <div className="flex items-center gap-1.5">
        <StateDot state={DOT[data.status]} label={data.status} />
        <span className="truncate text-[12.5px] font-medium text-fg">{data.label}</span>
        {data.checkpointed ? (
          <Save className="ml-auto size-3 shrink-0 text-subtle" aria-label="checkpoint saved" data-testid="checkpoint-icon" />
        ) : null}
      </div>
      <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-subtle">
        <span className="num">{data.durationMs !== null ? formatDuration(data.durationMs) : data.status === "idle" ? "–" : "…"}</span>
        {data.round ? (
          <span data-testid="debate-round">
            {t("รอบ", "Round")} {data.round.round}/{data.round.of}
          </span>
        ) : null}
        {data.tools ? (
          <span className="ml-auto inline-flex items-center gap-0.5 rounded-sm bg-layer-2 px-1" title={`${data.tools} tool calls`}>
            <Wrench className="size-2.5" aria-hidden />
            <span className="num">{data.tools}</span>
          </span>
        ) : null}
      </div>
      <Handle type="source" position={Position.Right} className="!size-1.5 !border-0 !bg-line-strong" />
    </div>
  );
});

const NODE_TYPES = { pipeline: PipelineNodeCard };

function toolCounts(view: RunView | undefined): Record<string, number> {
  const counts: Record<string, number> = {};
  if (!view) return counts;
  for (const c of Object.values(view.calls)) if (c.node) counts[c.node] = (counts[c.node] ?? 0) + 1;
  return counts;
}

export interface PipelineGraphProps {
  graph: GraphView;
  analysts: AnalystName[];
  view?: RunView;
  input?: Record<string, unknown>;
  /** ticking wall clock for live durations; without it running nodes show "…" */
  now?: number;
  /** the run died without run.finished (server crash): running nodes are shown failed */
  stalled?: boolean;
  selected?: string | null;
  onSelect?: (node: string) => void;
  height?: number;
}

export function PipelineGraph({
  graph,
  analysts,
  view,
  input = {},
  now,
  stalled = false,
  selected = null,
  onSelect,
  height = 420,
}: PipelineGraphProps) {
  const pipeline = useMemo(() => buildPipeline(graph, analysts), [graph, analysts]);
  const positions = useMemo(() => layoutPipeline(pipeline.nodes, pipeline.edges), [pipeline]);
  const tools = useMemo(() => toolCounts(view), [view]);
  const taken = useMemo(() => takenEdges(view?.path ?? []), [view?.path]);

  const fresh: Node<PipelineNodeData>[] = pipeline.nodes.map((n) => {
    const run = view?.nodes[n.id];
    let status: PipelineNodeData["status"] = run?.status ?? "idle";
    if (stalled && status === "running") status = "error";
    // live elapsed time only with a ticking `now` from the caller (never Date.now() per render)
    const live = now !== undefined && run?.status === "running" && run.startedAt !== null ? now - run.startedAt : null;
    return {
      id: n.id,
      type: "pipeline",
      position: positions[n.id] ?? { x: 0, y: 0 },
      draggable: false,
      connectable: false,
      data: {
        label: n.label,
        kind: n.kind,
        status,
        durationMs: run?.durationMs ?? (stalled ? null : live),
        tools: tools[n.id] ?? 0,
        checkpointed: run?.checkpointed ?? false,
        round: run ? debateRound(n.id, run.runs, input) : null,
        selected: selected === n.id,
      },
    };
  });

  // React Flow keeps each node's measured size/handles on the node object: hold the nodes in
  // state (applyNodeChanges) and merge fresh data in, or a live run — whose node data changes
  // every tick — would leave the nodes hidden and the edges undrawn, waiting for a re-measure.
  const [nodes, setNodes] = useState<Node<PipelineNodeData>[]>(fresh);
  const freshKey = JSON.stringify(fresh.map((n) => [n.id, n.position, n.data]));
  useEffect(() => {
    setNodes((prev) => {
      const byId = new Map(prev.map((p) => [p.id, p]));
      return fresh.map((n) => {
        const p = byId.get(n.id);
        return p ? { ...p, position: n.position, data: n.data } : n;
      });
    });
    // freshKey stands for `fresh`
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [freshKey]);
  const onNodesChange = useCallback(
    (changes: NodeChange<Node<PipelineNodeData>>[]) => setNodes((ns) => applyNodeChanges(changes, ns)),
    [],
  );

  const edges: Edge[] = pipeline.edges.map((e) => {
    const used = taken.has(e.id);
    const active = used && view?.nodes[e.target]?.status === "running";
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      label: e.conditional && e.label ? e.label : undefined,
      animated: active,
      className: used ? "fh-edge-taken" : undefined,
      style: {
        stroke: used ? "var(--fh-accent)" : "var(--fh-border-strong)",
        strokeWidth: used ? 2 : 1,
        strokeDasharray: e.conditional ? "5 4" : undefined,
        opacity: used || !view ? 1 : 0.55,
      },
      labelStyle: { fontSize: 10, fill: "var(--fh-text-subtle)" },
      labelBgStyle: { fill: "var(--fh-bg)" },
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: used ? "var(--fh-accent)" : "var(--fh-border-strong)" },
      data: { taken: used },
    };
  });

  return (
    <div data-testid="pipeline-graph" className="fh-flow overflow-hidden rounded-md border border-line bg-bg" style={{ height }}>
      <ReactFlow
        nodes={nodes}
        onNodesChange={onNodesChange}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.06, minZoom: 0.55 }}
        minZoom={0.4}
        maxZoom={1.5}
        nodesDraggable={false}
        nodesConnectable={false}
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_, node) => onSelect?.(node.id)}
      >
        <Background gap={16} size={1} color="var(--fh-border)" />
      </ReactFlow>
    </div>
  );
}
