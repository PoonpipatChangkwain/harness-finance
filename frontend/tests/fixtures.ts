import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import type { EventEnvelope } from "@/lib/events";
import type { GraphView, TradeRunDetail } from "@/lib/trade-api";

export const FIXTURE_DIR = path.join(import.meta.dirname, "fixtures");

export const FIXTURE_NAMES = readdirSync(FIXTURE_DIR)
  .filter((f) => f.startsWith("research-run-") && f.endsWith(".jsonl"))
  .map((f) => f.slice("research-run-".length, -".jsonl".length))
  .sort();

export function loadFixture(name: string): EventEnvelope[] {
  const text = readFileSync(path.join(FIXTURE_DIR, `research-run-${name}.jsonl`), "utf8");
  return text
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line) as EventEnvelope);
}

/** Trading runs recorded by backend tests/harness/trading/test_frontend_fixtures.py (P11). */
export const TRADING_FIXTURES = readdirSync(FIXTURE_DIR)
  .filter((f) => f.startsWith("trading-run-") && f.endsWith(".jsonl"))
  .map((f) => f.slice("trading-run-".length, -".jsonl".length))
  .sort();

export function loadTradingRun(name: string): EventEnvelope[] {
  const text = readFileSync(path.join(FIXTURE_DIR, `trading-run-${name}.jsonl`), "utf8");
  return text
    .split("\n")
    .filter((line) => line.trim())
    .map((line) => JSON.parse(line) as EventEnvelope);
}

export function loadTradingDetail(name: string): TradeRunDetail {
  return JSON.parse(readFileSync(path.join(FIXTURE_DIR, `trading-run-${name}.detail.json`), "utf8")) as TradeRunDetail;
}

export function loadTradingGraph(): GraphView {
  return JSON.parse(readFileSync(path.join(FIXTURE_DIR, "trading-graph.json"), "utf8")) as GraphView;
}

/** Deterministic shuffle (mulberry32) so a failing order can be reproduced. */
export function shuffled<T>(items: T[], seed = 42): T[] {
  let a = seed >>> 0;
  const rand = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const out = items.slice();
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [out[i], out[j]] = [out[j] as T, out[i] as T];
  }
  return out;
}
