"use client";

/**
 * The bull/bear ML model's read at the last closed bar (state.ml_signal, computed by
 * `compute/ml/bull_bear.py`). It is evidence for the desk, not an order: the card shows the
 * probabilities, the signal and the walk-forward track record the desk was told about.
 */

import { clsx } from "clsx";

import type { MlSignal } from "@/lib/trade-api";

const SIGNAL_CLASS: Record<string, string> = {
  long: "bg-up/15 text-up",
  short: "bg-down/15 text-down",
  flat: "bg-layer-2 text-muted",
};

const pct = (x: number | null | undefined) => (typeof x === "number" ? `${Math.round(x * 100)}%` : "–");
const fixed = (x: number | null | undefined, d = 2) => (typeof x === "number" ? x.toFixed(d) : "–");

function Bar({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-14 text-muted">{label}</span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-layer-2">
        <div className={clsx("h-full", tone)} style={{ width: `${Math.min(100, Math.max(0, value * 100))}%` }} />
      </div>
      <span className="num w-10 text-right">{pct(value)}</span>
    </div>
  );
}

export function MlSignalCard({ ml }: { ml: MlSignal | null | undefined }) {
  if (!ml) return null;
  if (!ml.available) {
    return (
      <section data-testid="ml-signal" className="rounded-md border border-line bg-layer-1 p-3">
        <h3 className="mb-1.5 text-[12px] font-medium uppercase tracking-wide text-subtle">ML Bull/Bear</h3>
        <p className="text-[12.5px] text-warning">โมเดลไม่พร้อมรอบนี้: {ml.error ?? "ไม่ทราบสาเหตุ"} (ไม่ได้แปลว่าสัญญาณเป็น flat)</p>
      </section>
    );
  }
  const signal = ml.signal ?? "flat";
  const tr = ml.track_record;
  const m = tr?.model;
  const al = tr?.always_long;
  return (
    <section data-testid="ml-signal" data-signal={signal} className="rounded-md border border-line bg-layer-1 p-3">
      <h3 className="mb-2 flex flex-wrap items-center gap-2 text-[12px] font-medium uppercase tracking-wide text-subtle">
        ML Bull/Bear
        <span className="normal-case tracking-normal">
          {ml.model} {ml.version} · {ml.timeframe}
          {ml.bar_ts ? ` · แท่ง ${new Date(ml.bar_ts).toLocaleString("th-TH", { dateStyle: "short", timeStyle: "short" })}` : ""}
        </span>
      </h3>
      <div className="mb-2 flex flex-wrap items-center gap-3 text-[12.5px]">
        <span className={clsx("rounded-sm px-2 py-0.5 text-[12px] font-semibold uppercase", SIGNAL_CLASS[signal])}>{signal}</span>
        <span className="text-muted">
          confidence <b className="num text-fg">{fixed(ml.confidence)}</b>
        </span>
        {ml.daily_trend ? <span className="text-muted">เทรนด์ D1: {ml.daily_trend}</span> : null}
        {ml.session ? <span className="text-muted">session: {ml.session}</span> : null}
        {ml.vix != null ? (
          <span className="text-muted">
            VIX <span className="num">{fixed(ml.vix, 1)}</span>
          </span>
        ) : null}
        {ml.is_nfp_day ? <span className="rounded-sm bg-warning/15 px-1.5 text-[11.5px] text-warning">NFP day</span> : null}
      </div>
      <div className="space-y-1 text-[12.5px]">
        <Bar label="P(bull)" value={ml.p_bull ?? 0} tone="bg-up" />
        <Bar label="P(bear)" value={ml.p_bear ?? 0} tone="bg-down" />
      </div>
      {ml.tp_r_long != null || ml.time_stop_bars != null ? (
        <p className="mt-2 text-[11.5px] text-subtle">
          P = โอกาสถึง TP (long +{ml.tp_r_long}R / short +{ml.tp_r_short}R) ก่อนโดน −1R ภายใน {ml.time_stop_bars} แท่ง
        </p>
      ) : null}
      {m ? (
        <p className="num mt-1 text-[11.5px] text-subtle">
          track record ({tr?.period}): {m.trades} เทรด · win {pct(m.win_rate)} · PF {fixed(m.profit_factor)} · always-long win{" "}
          {pct(al?.win_rate)} PF {fixed(al?.profit_factor)}
        </p>
      ) : null}
      {ml.missing_features?.length ? (
        <p className="mt-1 text-[11.5px] text-warning">feature ขาด {ml.missing_features.length} ตัว (โมเดลคิดโดยปล่อยเป็น NaN)</p>
      ) : null}
    </section>
  );
}
