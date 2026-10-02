/**
 * Pure helpers of the Trade page (phase-11): the pipeline graph shown for a run,
 * analyst defaults per asset type, debate rounds, symbol badges. No numbers are
 * computed here (F1) — only structure and labels.
 */

import type { SymbolMatch } from "./api";
import type { AnalystName, GraphView } from "./trade-api";

export const ALL_ANALYSTS: AnalystName[] = ["market", "news", "fundamentals", "sentiment", "macro"];

export const ANALYST_LABEL: Record<AnalystName, string> = {
  market: "Market",
  news: "News",
  fundamentals: "Fundamentals",
  sentiment: "Sentiment",
  macro: "Macro",
};

/** Mirrors backend `default_analysts` (the backend stays the authority: `select_analysts`). */
export function defaultAnalysts(assetType: string | null | undefined): AnalystName[] {
  if (assetType === "stock") return ["market", "news", "fundamentals", "sentiment"];
  if (assetType === "crypto") return ["market", "news", "sentiment"];
  return ["market", "news", "macro"];
}

/** Analysts offered in the setup bar: no company behind a pair/commodity/coin → no fundamentals. */
export function allowedAnalysts(assetType: string | null | undefined): AnalystName[] {
  return assetType === "stock" ? ALL_ANALYSTS : ALL_ANALYSTS.filter((a) => a !== "fundamentals");
}

/** Badge text for a symbol (ui-spec §5): stock·TH, stock·US, forex, crypto, commodity. */
export function assetBadge(symbol: Pick<SymbolMatch["symbol"], "asset_type" | "market">): string {
  return symbol.asset_type === "stock" ? `stock·${symbol.market}` : symbol.asset_type;
}

export const SUB_ANALYST_PREFIX = "analyst.";
export const isAnalystSubNode = (id: string) => id.startsWith(SUB_ANALYST_PREFIX);
export const analystNodeId = (name: string) => `${SUB_ANALYST_PREFIX}${name}`;

export const INVEST_DEBATE_NODES = ["bull_researcher", "bear_researcher"] as const;
export const RISK_DEBATE_NODES = ["aggressive", "conservative", "neutral"] as const;

export type DebateKind = "invest" | "risk";

export function debateKindOf(node: string | null | undefined): DebateKind | null {
  if (!node) return null;
  if ((INVEST_DEBATE_NODES as readonly string[]).includes(node) || node === "research_manager") return "invest";
  if ((RISK_DEBATE_NODES as readonly string[]).includes(node) || node === "portfolio_manager") return "risk";
  return null;
}

/** "round 2/3" for a debate node: each speaker runs once per round. */
export function debateRound(node: string, runs: number, input: Record<string, unknown>): { round: number; of: number } | null {
  const kind = debateKindOf(node);
  if (!kind || node === "research_manager" || node === "portfolio_manager" || runs < 1) return null;
  const of = Number(kind === "invest" ? (input.debate_rounds ?? 1) : (input.risk_rounds ?? 1)) || 1;
  return { round: Math.min(runs, of), of };
}

export interface PipelineNode {
  id: string;
  label: string;
  kind: string;
  phase: string;
  /** set on per-analyst nodes */
  analyst?: AnalystName;
}

export interface PipelineEdge {
  id: string;
  source: string;
  target: string;
  /** router key; conditional edges are drawn dashed */
  label: string | null;
  conditional: boolean;
}

/**
 * The run's pipeline as drawn: the backend graph with the single `analysts` node
 * expanded into one node per selected analyst, END/self-loop/`__fallback__` edges
 * dropped, and parallel router keys to the same target merged into one edge.
 */
export function buildPipeline(graph: GraphView, analysts: AnalystName[]): { nodes: PipelineNode[]; edges: PipelineEdge[] } {
  const nodes: PipelineNode[] = [];
  for (const n of graph.nodes) {
    if (n.id === "analysts") {
      for (const a of analysts) {
        nodes.push({ id: analystNodeId(a), label: ANALYST_LABEL[a], kind: "agent", phase: n.phase, analyst: a });
      }
    } else {
      nodes.push({ id: n.id, label: n.label, kind: n.kind, phase: n.phase });
    }
  }
  const expand = (id: string) => (id === "analysts" ? analysts.map(analystNodeId) : [id]);
  const merged = new Map<string, PipelineEdge>();
  for (const e of graph.edges) {
    if (e.target === "END" || e.source === e.target || e.label === "__fallback__") continue;
    for (const source of expand(e.source)) {
      for (const target of expand(e.target)) {
        const id = `${source}->${target}`;
        const prev = merged.get(id);
        const label = e.label ?? null;
        if (prev) {
          if (label && prev.label !== label) prev.label = prev.label ? `${prev.label}·${label}` : label;
        } else {
          merged.set(id, { id, source, target, label, conditional: label !== null });
        }
      }
    }
  }
  return { nodes, edges: [...merged.values()] };
}

/** Edge ids actually traversed, from the node start order of the run (reducer `path`). */
export function takenEdges(path: string[]): Set<string> {
  const taken = new Set<string>();
  const graphPath = path.filter((n) => !isAnalystSubNode(n));
  const subs = path.filter(isAnalystSubNode);
  for (let i = 1; i < graphPath.length; i++) {
    const prev = graphPath[i - 1] as string;
    const next = graphPath[i] as string;
    if (prev === "analysts") {
      for (const s of subs) taken.add(`${s}->${next}`);
    } else if (next === "analysts") {
      for (const s of subs) taken.add(`${prev}->${s}`);
    } else {
      taken.add(`${prev}->${next}`);
    }
  }
  return taken;
}

/** yyyy-mm-dd of today in the viewer's zone (the setup bar's default date). */
export function todayIso(now = new Date()): string {
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

export const EXAMPLE_SYMBOLS = ["PTT", "AAPL", "EURUSD", "XAUUSD", "BTCUSD"] as const;
