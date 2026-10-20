"use client";

/**
 * Price chart (ui-spec §5, phase-11 §11.2) on lightweight-charts v5: candles + volume,
 * SMA20/50/200 + Bollinger overlays, optional RSI / MACD panes, timeframes 1m–1D, range
 * buttons for 1D, and the decision's entry / stop / targets as price lines. Every indicator
 * value comes from `GET /market/{symbol}/indicators` (backend compute, F1) — nothing is
 * computed here. When the chart shows the present (no past `asOf`) it polls both endpoints
 * and patches the newest bars in place with `series.update`, so zoom and scroll survive.
 * Colors follow the design tokens and are re-read when the theme changes.
 */

import { clsx } from "clsx";
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type SeriesType,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

import { CitationChip } from "@/components/conversation/CitationChip";
import {
  tradeApi,
  type ChartInterval,
  type ChartRange,
  type Indicators,
  type IndicatorSet,
  type Ohlcv,
} from "@/lib/trade-api";
import { intlLocale, useT } from "@/lib/i18n";

const RANGES: { id: ChartRange; label: string }[] = [
  { id: "1mo", label: "1M" },
  { id: "3mo", label: "3M" },
  { id: "6mo", label: "6M" },
  { id: "1y", label: "1Y" },
  { id: "5y", label: "5Y" },
];

interface Timeframe {
  id: ChartInterval;
  label: string;
  range: ChartRange | null; // null = the range buttons pick it
  pollMs: number;
}

const DAILY: Timeframe = { id: "1d", label: "1D", range: null, pollMs: 300_000 };

/** Intraday timeframes have a fixed window (Yahoo only serves ~7d of 1m, ~60d of 5m/15m). */
export const TIMEFRAMES: Timeframe[] = [
  { id: "1m", label: "1m", range: "1d", pollMs: 15_000 },
  { id: "5m", label: "5m", range: "5d", pollMs: 30_000 },
  { id: "15m", label: "15m", range: "5d", pollMs: 60_000 },
  { id: "1h", label: "1H", range: "1mo", pollMs: 60_000 },
  DAILY,
];

const OVERLAYS: IndicatorSet[] = ["sma20", "sma50", "sma200", "bb20"];
const TF_KEY = "fh.trade.chart.tf";
const INTRADAY_VISIBLE_BARS = 150;

export interface PriceLineSpec {
  price: number;
  title: string;
  kind: "entry" | "stop" | "target";
  /** a limit/stop entry that has not filled yet: drawn dashed like the levels */
  pending?: boolean;
}

function token(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/**
 * Chart time for a bar timestamp. Daily bars are dates; intraday bars are unix seconds shifted
 * to the viewer's wall clock, since lightweight-charts renders timestamps as UTC.
 */
export function chartTime(ts: string, intraday: boolean): Time {
  if (!intraday) return ts.slice(0, 10) as Time;
  const d = new Date(ts);
  return (Math.floor(d.getTime() / 1000) - d.getTimezoneOffset() * 60) as UTCTimestamp;
}

/** True when `asOf` is absent or not in the past, i.e. the chart shows the present. */
export function isLiveAsOf(asOf: string | null | undefined, now: Date = new Date()): boolean {
  if (!asOf) return true;
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  return asOf >= today;
}

function readTimeframe(): ChartInterval {
  try {
    const saved = window.localStorage.getItem(TF_KEY);
    if (TIMEFRAMES.some((t) => t.id === saved)) return saved as ChartInterval;
  } catch {
    // storage unavailable (private mode, blocked site data) — fall back to 1D
  }
  return "1d";
}

function useThemeKey(): string {
  const [key, setKey] = useState("");
  useEffect(() => {
    const root = document.documentElement;
    const update = () => setKey(root.getAttribute("data-theme") ?? "");
    update();
    const obs = new MutationObserver(update);
    obs.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  return key;
}

interface Loaded {
  key: string;
  intraday: boolean;
  ohlcv: Ohlcv;
  ind: Indicators | null;
}

/** Live series handles of the chart currently on screen, for in-place poll updates. */
interface Handles {
  key: string;
  intraday: boolean;
  colors: { up: string; down: string };
  candles: ISeriesApi<"Candlestick">;
  volume: ISeriesApi<"Histogram">;
  lines: Map<string, ISeriesApi<SeriesType>>;
  lastBarMs: number;
  lastIndMs: number;
}

function applyPoll(h: Handles, ohlcv: Ohlcv, ind: Indicators | null): void {
  try {
    for (const b of ohlcv.bars) {
      const ms = Date.parse(b.ts);
      if (ms < h.lastBarMs) continue;
      const time = chartTime(b.ts, h.intraday);
      const [open, close] = [Number(b.open), Number(b.close)];
      h.candles.update({ time, open, high: Number(b.high), low: Number(b.low), close });
      h.volume.update({ time, value: b.volume, color: close >= open ? `${h.colors.up}66` : `${h.colors.down}66` });
      h.lastBarMs = ms;
    }
    if (!ind) return;
    let last = h.lastIndMs;
    ind.ts.forEach((t, i) => {
      const ms = Date.parse(t);
      if (ms < h.lastIndMs) return;
      for (const [key, series] of h.lines) {
        const v = ind.series[key]?.[i];
        if (v !== null && v !== undefined) series.update({ time: chartTime(t, h.intraday), value: v });
      }
      last = ms;
    });
    h.lastIndMs = last;
  } catch {
    // a bar older than the series tail (provider reshuffle) — the next full load fixes it
  }
}

export function PriceChart({
  symbol,
  runId,
  asOf = null,
  lines = [],
  height = 360,
  initialInterval,
}: {
  symbol: string;
  runId?: string | null;
  asOf?: string | null;
  lines?: PriceLineSpec[];
  height?: number;
  initialInterval?: ChartInterval;
}) {
  const t = useT();
  const el = useRef<HTMLDivElement>(null);
  const [tf, setTf] = useState<ChartInterval>("1d");
  const [range, setRange] = useState<ChartRange>("6mo");
  const [panes, setPanes] = useState<{ rsi: boolean; macd: boolean }>({ rsi: false, macd: false });
  const [ohlcv, setOhlcv] = useState<Ohlcv | null>(null);
  const [gen, setGen] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [stale, setStale] = useState(false);
  const loaded = useRef<Loaded | null>(null);
  const handles = useRef<Handles | null>(null);
  const theme = useThemeKey();

  const frame = TIMEFRAMES.find((t) => t.id === tf) ?? DAILY;
  const intraday = tf !== "1d";
  const effRange = frame.range ?? range;
  const live = isLiveAsOf(asOf);
  const effAsOf = live ? null : asOf; // "as of today" is the live chart; don't cut today's bars
  const dataKey = `${symbol}|${tf}|${effRange}|${effAsOf ?? ""}`;
  const sets: IndicatorSet[] = [...OVERLAYS, "rsi14", "macd"];

  // a run's page opens on the timeframe it was analysed on; otherwise the viewer's last choice
  useEffect(() => setTf(initialInterval ?? readTimeframe()), [initialInterval]);

  const chooseTf = (id: ChartInterval) => {
    setTf(id);
    try {
      window.localStorage.setItem(TF_KEY, id);
    } catch {
      // not persisted — fine
    }
  };

  // full load whenever what is shown changes
  useEffect(() => {
    let alive = true;
    setError(null);
    Promise.all([
      tradeApi.ohlcv(symbol, tf, effRange, effAsOf),
      tradeApi.indicators(symbol, sets, tf, effRange, effAsOf).catch(() => null),
    ])
      .then(([o, i]) => {
        if (!alive) return;
        loaded.current = { key: dataKey, intraday, ohlcv: o, ind: i };
        setOhlcv(o);
        setUpdatedAt(new Date());
        setStale(false);
        setGen((g) => g + 1);
      })
      .catch((err: unknown) => alive && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      alive = false;
    };
    // `sets` is constant and `dataKey` is derived from the listed deps
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, tf, effRange, effAsOf]);

  // build the chart from the last full load
  const linesKey = JSON.stringify(lines);
  useEffect(() => {
    const data = loaded.current;
    if (!el.current || !data) return;
    const { ohlcv: o, ind, intraday: isIntraday } = data;
    const colors = {
      bg: token("--fh-bg", "#0d1014"),
      text: token("--fh-text-muted", "#a3adba"),
      grid: token("--fh-border", "#262e38"),
      up: token("--fh-up", "#3fb97f"),
      down: token("--fh-down", "#f06a6a"),
      accent: token("--fh-accent", "#4ea1ff"),
      warn: token("--fh-state-warning", "#e6a23c"),
      subtle: token("--fh-text-subtle", "#6f7b89"),
    };
    const precision = o.asset_type === "forex" ? 5 : 2; // forex: show pips
    const chart: IChartApi = createChart(el.current, {
      autoSize: true,
      layout: { background: { color: colors.bg }, textColor: colors.text, fontSize: 11 },
      grid: { vertLines: { color: colors.grid }, horzLines: { color: colors.grid } },
      rightPriceScale: { borderColor: colors.grid },
      timeScale: { borderColor: colors.grid, timeVisible: isIntraday, secondsVisible: false },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: colors.up,
      downColor: colors.down,
      borderVisible: false,
      wickUpColor: colors.up,
      wickDownColor: colors.down,
      priceFormat: { type: "price", precision, minMove: 1 / 10 ** precision },
    });
    candles.setData(
      o.bars.map((b) => ({
        time: chartTime(b.ts, isIntraday),
        open: Number(b.open),
        high: Number(b.high),
        low: Number(b.low),
        close: Number(b.close),
      })),
    );
    const volume = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "vol" });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    volume.setData(
      o.bars.map((b) => ({
        time: chartTime(b.ts, isIntraday),
        value: b.volume,
        color: Number(b.close) >= Number(b.open) ? `${colors.up}66` : `${colors.down}66`,
      })),
    );

    const lineHandles = new Map<string, ISeriesApi<SeriesType>>();
    if (ind) {
      const line = (key: string, color: string, pane = 0, style: LineStyle = LineStyle.Solid) => {
        const values = ind.series[key];
        if (!values) return;
        const s = chart.addSeries(LineSeries, { color, lineWidth: 1, lineStyle: style, priceLineVisible: false, lastValueVisible: false }, pane);
        s.setData(
          ind.ts.flatMap((t, i) =>
            values[i] === null || values[i] === undefined ? [] : [{ time: chartTime(t, isIntraday), value: values[i] as number }],
          ),
        );
        lineHandles.set(key, s);
      };
      line("sma20", colors.accent);
      line("sma50", colors.warn);
      line("sma200", colors.subtle);
      line("bb_upper", colors.subtle, 0, LineStyle.Dotted);
      line("bb_lower", colors.subtle, 0, LineStyle.Dotted);
      let pane = 1;
      if (panes.rsi) line("rsi14", colors.accent, pane++);
      if (panes.macd) {
        line("macd", colors.accent, pane);
        line("macd_signal", colors.warn, pane);
      }
    }

    for (const l of lines) {
      candles.createPriceLine({
        price: l.price,
        title: l.title,
        color: l.kind === "stop" ? colors.down : l.kind === "target" ? colors.up : colors.accent,
        lineWidth: 1,
        lineStyle: l.kind === "entry" && !l.pending ? LineStyle.Solid : LineStyle.Dashed,
        axisLabelVisible: true,
      });
    }
    for (const p of chart.panes().slice(1)) p.setStretchFactor(0.35);
    const n = o.bars.length;
    if (isIntraday && n > INTRADAY_VISIBLE_BARS) {
      chart.timeScale().setVisibleLogicalRange({ from: n - INTRADAY_VISIBLE_BARS, to: n + 3 });
    } else {
      chart.timeScale().fitContent();
    }

    const lastTs = (ts: string[]) => {
      const last = ts.at(-1);
      return last ? Date.parse(last) : 0;
    };
    handles.current = {
      key: data.key,
      intraday: isIntraday,
      colors: { up: colors.up, down: colors.down },
      candles,
      volume,
      lines: lineHandles,
      lastBarMs: lastTs(o.bars.map((b) => b.ts)),
      lastIndMs: ind ? lastTs(ind.ts) : 0,
    };
    return () => {
      handles.current = null;
      chart.remove();
    };
    // linesKey stands for `lines`
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gen, panes, theme, linesKey]);

  // live: poll and patch the newest bars in place
  useEffect(() => {
    if (!live) return;
    let alive = true;
    const tick = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const [o, i] = await Promise.all([
          tradeApi.ohlcv(symbol, tf, effRange, null),
          tradeApi.indicators(symbol, sets, tf, effRange, null).catch(() => null),
        ]);
        if (!alive) return;
        const h = handles.current;
        if (h && h.key === dataKey) applyPoll(h, o, i);
        loaded.current = { key: dataKey, intraday, ohlcv: o, ind: i }; // a pane/theme rebuild starts from fresh data
        setOhlcv(o);
        setUpdatedAt(new Date());
        setStale(false);
      } catch {
        if (alive) setStale(true);
      }
    };
    const id = window.setInterval(() => void tick(), frame.pollMs);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live, symbol, tf, effRange, frame.pollMs]);

  const btn = (active: boolean) =>
    clsx("rounded-sm px-1.5 py-0.5", active ? "bg-accent-muted text-accent" : "text-muted hover:bg-layer-2");

  return (
    <section data-testid="price-chart" className="rounded-md border border-line bg-layer-1">
      <div className="flex flex-wrap items-center gap-1 border-b border-line px-2 py-1 text-[12px]">
        {TIMEFRAMES.map((t) => (
          <button key={t.id} type="button" onClick={() => chooseTf(t.id)} className={btn(tf === t.id)}>
            {t.label}
          </button>
        ))}
        {!intraday ? (
          <>
            <span className="mx-1 h-4 w-px bg-line" />
            {RANGES.map((r) => (
              <button key={r.id} type="button" onClick={() => setRange(r.id)} className={btn(range === r.id)}>
                {r.label}
              </button>
            ))}
          </>
        ) : null}
        <span className="mx-1 h-4 w-px bg-line" />
        {(["rsi", "macd"] as const).map((k) => (
          <label key={k} className="flex items-center gap-1 text-muted">
            <input type="checkbox" checked={panes[k]} onChange={() => setPanes((p) => ({ ...p, [k]: !p[k] }))} />
            {k.toUpperCase()}
          </label>
        ))}
        <span className="ml-auto flex items-center gap-2 text-[11px] text-subtle">
          <span>
            <span className="text-accent">SMA20</span> · <span className="text-warning">SMA50</span> · SMA200 · BB20
          </span>
          {ohlcv ? (
            <span className="num">
              {ohlcv.symbol} · {ohlcv.provider}
              {ohlcv.provenance_id && runId ? <CitationChip marker={0} provenanceId={ohlcv.provenance_id} runId={runId} /> : null}
            </span>
          ) : null}
          {live && updatedAt ? (
            <span data-testid="chart-live" className={clsx("num flex items-center gap-1", stale && "text-warning")}>
              <span className={clsx("h-1.5 w-1.5 rounded-full", stale ? "bg-warning" : "animate-pulse bg-up")} />
              {stale ? t("อัปเดตไม่ได้", "Update failed") : "LIVE"} {updatedAt.toLocaleTimeString(intlLocale(), { hour12: false })}
            </span>
          ) : null}
          {!live && asOf ? <span className="text-warning">as of {asOf}</span> : null}
        </span>
      </div>
      {error ? <p className="p-3 text-[12.5px] text-error">{t("โหลดกราฟไม่ได้", "Could not load the chart")}: {error}</p> : null}
      <div ref={el} style={{ height }} className={clsx(!ohlcv && "fh-shimmer")} />
    </section>
  );
}
