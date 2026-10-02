"use client";

/**
 * /backtest (P15 §UI): no id → setup; an id → live progress (SSE: `backtest.progress`,
 * `backtest.cell`, `backtest.sim`) and, once simulated, the results with the what-if panel.
 */

import { AlertTriangle, Play, Square } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { BacktestSetup } from "@/components/backtest/BacktestSetup";
import { CellGrid } from "@/components/backtest/CellGrid";
import { EquityChart } from "@/components/backtest/EquityChart";
import { ResultTiles } from "@/components/backtest/ResultTiles";
import { TradesTable } from "@/components/backtest/TradesTable";
import { WhatIfPanel } from "@/components/backtest/WhatIfPanel";
import { StateDot, dotFor } from "@/components/ui/StateDot";
import { api } from "@/lib/api";
import { applyCellEvent } from "@/lib/backtest";
import { backtestApi, type BacktestCell, type BacktestDetail, type Sim } from "@/lib/backtest-api";
import type { EventPayloads } from "@/lib/events";
import { formatCost } from "@/lib/format";
import { subscribeRun } from "@/lib/sse";

type Progress = EventPayloads["backtest.progress"];

const PHASE_LABEL: Record<Progress["phase"], string> = {
  preload: "กำลังโหลดราคา",
  decisions: "ระบบเทรดกำลังตัดสินใจทีละวัน",
  simulate: "กำลังจำลองพอร์ต",
  done: "เสร็จแล้ว",
};

export function BacktestView({ id }: { id: string | null }) {
  const router = useRouter();
  if (!id) return <BacktestSetup onStarted={(bid) => router.push(`/backtest/${bid}`)} />;
  return <BacktestRun id={id} />;
}

function BacktestRun({ id }: { id: string }) {
  const [detail, setDetail] = useState<BacktestDetail | null>(null);
  const [cells, setCells] = useState<BacktestCell[]>([]);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [sim, setSim] = useState<Sim | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await backtestApi.detail(id);
      setDetail(d);
      setCells(d.cells);
      setSim((s) => (s && d.sims.some((x) => x.sim_id === s.sim_id) ? s : d.latest_sim));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  // live updates while it runs (the stream replays from the start, then follows)
  useEffect(() => {
    const ctrl = new AbortController();
    void subscribeRun(id, {
      signal: ctrl.signal,
      onEvent: (e) => {
        if (e.type === "backtest.progress") setProgress(e.payload as Progress);
        else if (e.type === "backtest.cell") setCells((cs) => applyCellEvent(cs, e.payload as EventPayloads["backtest.cell"]));
        else if (e.type === "backtest.sim" || e.type === "run.finished") void load();
        else if (e.type === "warning") {
          const msg = (e.payload as EventPayloads["warning"]).message;
          setWarnings((w) => (w.includes(msg) ? w : [...w, msg]));
        } else if (e.type === "guard.limit") {
          const msg = (e.payload as EventPayloads["guard.limit"]).detail;
          setWarnings((w) => (w.includes(msg) ? w : [...w, msg]));
        }
      },
    });
    return () => ctrl.abort();
  }, [id, load]);

  if (error && !detail) return <p role="alert" className="text-error">{error}</p>;
  if (!detail) return <p className="text-subtle">กำลังโหลด…</p>;

  const running = detail.running || detail.status === "running";
  const resumable = !running && ["interrupted", "cancelled", "failed", "limited"].includes(detail.status);
  const done = cells.filter((c) => c.status === "done").length;
  const failed = cells.filter((c) => c.status === "failed").length;
  const cost = progress?.cost_usd ?? detail.output?.cost_usd ?? cells.reduce((a, c) => a + (c.usage?.cost_usd ?? 0), 0);
  const pct = cells.length ? Math.round(((done + failed) / cells.length) * 100) : 0;

  const pickSim = async (simId: string) => {
    try {
      setSim(await backtestApi.sim(id, simId));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div data-testid="backtest-run" className="space-y-4">
      <header className="flex flex-wrap items-center gap-3">
        <StateDot state={dotFor(running ? "running" : detail.status)} label={detail.status} />
        <h1 className="text-[16px] font-semibold">{detail.name || detail.symbols.join(", ")}</h1>
        <span className="num text-[12.5px] text-muted">
          {detail.start_date} → {detail.end_date} · {detail.horizon} · {detail.symbols.join(", ")}
        </span>
        <span className="ml-auto text-[12.5px] text-muted">
          ค่า LLM <b className="num text-fg">{formatCost(cost)}</b>
        </span>
        {running ? (
          <button
            type="button"
            data-testid="bt-cancel"
            onClick={() => void api.cancelRun(id).then(load)}
            className="inline-flex items-center gap-1 rounded-md border border-line px-2.5 py-1 text-[12.5px] hover:bg-layer-2"
          >
            <Square className="size-3.5" aria-hidden /> หยุด
          </button>
        ) : null}
        {resumable ? (
          <button
            type="button"
            data-testid="bt-resume"
            onClick={() => void api.resumeRun(id).then(load)}
            className="inline-flex items-center gap-1 rounded-md bg-accent px-2.5 py-1 text-[12.5px] font-medium text-bg"
          >
            <Play className="size-3.5" aria-hidden /> ทำต่อ
          </button>
        ) : null}
      </header>

      <section className="space-y-2 rounded-md border border-line bg-layer-1 p-3">
        <div className="flex flex-wrap items-center gap-3 text-[12.5px]">
          <span className="font-medium">{progress ? PHASE_LABEL[progress.phase] : running ? "กำลังเริ่ม…" : detail.status}</span>
          <span className="num text-muted">
            {done + failed}/{cells.length} วัน·symbol · ใช้ผลเดิม {cells.filter((c) => c.cached_from).length} · ล้มเหลว {failed}
          </span>
          {detail.run.error ? <span className="text-error">{detail.run.error}</span> : null}
        </div>
        <div className="h-1.5 overflow-hidden rounded-full bg-layer-2" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
          <div className="h-full bg-accent transition-[width]" style={{ width: `${pct}%` }} />
        </div>
        <CellGrid cells={cells} />
        {warnings.length ? (
          <ul className="space-y-0.5 text-[12px] text-warning">
            {warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        ) : null}
      </section>

      {sim ? (
        <>
          <ResultTiles m={sim.metrics} />
          <EquityChart result={sim.result} currency={sim.metrics.currency} />
          {sim.result.baseline ? (
            <p className="text-[12px] text-muted">
              {sim.result.baseline.name} (ไม่ใช้ LLM, เงื่อนไขพอร์ตเดียวกัน): return{" "}
              <b className="num">{(sim.result.baseline.metrics.total_return * 100).toFixed(2)}%</b> · {sim.result.baseline.metrics.trades} เทรด
            </p>
          ) : null}
          <WhatIfPanel backtestId={id} sims={detail.sims} current={sim} disabled={running} onSim={(s) => { setSim(s); void load(); }} onPick={(sid) => void pickSim(sid)} />
          <TradesTable result={sim.result} />
        </>
      ) : null}

      <aside data-testid="bt-caveats" className="flex gap-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-[12px] text-muted">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
        <ul className="list-disc space-y-0.5 pl-4">
          <li>ผลในอดีตไม่รับประกันอนาคต และระบบนี้ไม่ส่งคำสั่งซื้อขายจริง</li>
          <li>ข้อมูลราคาถูกตัดที่วันตัดสินใจ แต่ตัวโมเดลเองอาจ &quot;รู้&quot; เหตุการณ์ในช่วงนั้นจากข้อมูลที่ใช้ฝึก — ช่วงหลัง training cutoff ของโมเดลจะเชื่อถือได้กว่า</li>
          <li>ข่าว/โซเชียลย้อนหลังมีไม่ครบ · ค่าคอมฯ และ slippage เป็นค่าที่คุณกำหนด · ปฏิทินเศรษฐกิจย้อนหลังไม่มี</li>
        </ul>
      </aside>
    </div>
  );
}
