"use client";

/**
 * (P17 §17.3) ReasoningBank tab: lessons distilled from past runs (no personal data).
 * Header stats (`/reasoning-bank/stats`), filters workflow / kind / q (debounced), a table
 * (title · kind · workflow · tags · uses · success rate · created) and a detail drawer with
 * the content, the latest uses and a retire button.
 */

import { clsx } from "clsx";
import { Archive, Search } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { shortId } from "@/lib/format";
import { tr, useT } from "@/lib/i18n";
import {
  memoryApi,
  type ReasoningDetailOut,
  type ReasoningItemOut,
  type ReasoningStats,
  type ReasoningTags,
} from "@/lib/memory-api";

import { Drawer, Pill, RatioBar, formatDate } from "./bits";

export const KIND_STYLE: Record<string, { label: string; className: string }> = {
  strategy: { label: "strategy", className: "bg-done/15 text-done" },
  pitfall: { label: "pitfall", className: "bg-error/15 text-error" },
  coaching: { label: "coaching", className: "bg-accent-muted text-accent" },
};

const WORKFLOWS = [
  {
    id: "",
    get label() {
      return tr("ทุก workflow", "All workflows");
    },
  },
  { id: "research", label: "research" },
  { id: "portfolio", label: "portfolio" },
  { id: "trade", label: "trade" },
];
const KINDS = [
  {
    id: "",
    get label() {
      return tr("ทุกชนิด", "All kinds");
    },
  },
  { id: "strategy", label: "strategy" },
  { id: "pitfall", label: "pitfall" },
  { id: "coaching", label: "coaching" },
];

const errText = (err: unknown) => (err instanceof Error ? err.message : String(err));

export function KindBadge({ kind }: { kind: string }) {
  const s = KIND_STYLE[kind] ?? { label: kind, className: "bg-layer-2 text-muted" };
  return (
    <Pill className={s.className}>
      <span data-testid="kind-badge">{s.label}</span>
    </Pill>
  );
}

/** Tags other than `workflow` as "key:value" chips. */
export function tagList(tags: ReasoningTags): string[] {
  return Object.entries(tags)
    .filter(([k, v]) => k !== "workflow" && v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}:${String(v)}`);
}

function Stats({ stats }: { stats: ReasoningStats | null }) {
  const t = useT();
  if (!stats) return null;
  const tiles: [string, string][] = [
    [t("ทั้งหมด", "Total"), String(stats.total)],
    [t("ใช้งานอยู่", "Active"), String(stats.active)],
    [t("success rate เฉลี่ย", "Avg success rate"), stats.avg_success_rate === null ? "–" : `${Math.round(stats.avg_success_rate * 100)}%`],
    ...Object.entries(stats.by_kind).map(([k, n]): [string, string] => [k, String(n)]),
  ];
  return (
    <div data-testid="rb-stats" className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
      {tiles.map(([label, value]) => (
        <div key={label} className="rounded-md border border-line bg-layer-1 px-3 py-2">
          <div className="text-[11px] text-subtle">{label}</div>
          <div className="num text-[16px] font-semibold">{value}</div>
        </div>
      ))}
    </div>
  );
}

function DetailDrawer({ id, onClose, onRetired }: { id: string; onClose: () => void; onRetired: () => void }) {
  const t = useT();
  const [detail, setDetail] = useState<ReasoningDetailOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    memoryApi
      .reasoningItem(id)
      .then(setDetail)
      .catch((err: unknown) => setError(errText(err)));
  }, [id]);
  useEffect(load, [load]);

  const retire = async () => {
    setBusy(true);
    try {
      await memoryApi.retireReasoning(id);
      load();
      onRetired();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  const item = detail?.item;
  return (
    <Drawer label={item?.title ?? t("บทเรียน", "Lesson")} onClose={onClose}>
      {error ? <p className="mb-2 text-error">{error}</p> : null}
      {!item && !error ? <div className="h-32 animate-pulse rounded-md bg-layer-2" /> : null}
      {item ? (
        <div data-testid="rb-detail" className="space-y-3">
          <div className="flex flex-wrap items-center gap-1.5">
            <KindBadge kind={item.kind} />
            <Pill>{item.tags.workflow}</Pill>
            {item.status !== "active" ? <Pill className="bg-layer-2 text-subtle">{item.status}</Pill> : null}
            {tagList(item.tags).map((t) => (
              <Pill key={t}>{t}</Pill>
            ))}
          </div>
          <p className="text-muted">{item.description}</p>
          <div className="whitespace-pre-wrap rounded-md bg-layer-2/60 p-2 text-[13px] text-fg">{item.content}</div>
          <dl className="num grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[12px]">
            <dt className="text-subtle">{t("ใช้ไป", "Used")}</dt>
            <dd>
              {t(`${item.use_count} ครั้ง · สำเร็จ ${item.success_count}`, `${item.use_count} times · ${item.success_count} succeeded`)}
            </dd>
            <dt className="text-subtle">success rate</dt>
            <dd>
              <RatioBar value={item.success_rate} label="success rate" />
            </dd>
            <dt className="text-subtle">{t("จาก run", "From runs")}</dt>
            <dd>
              {t(`${item.source_count} แหล่ง`, `${item.source_count} source(s)`)}
              {item.source_run_id ? ` · ${shortId(item.source_run_id)}` : ""}
            </dd>
            <dt className="text-subtle">{t("ผลที่มา", "Outcome")}</dt>
            <dd>
              {item.outcome_signal}
              {item.judge_score != null ? ` (${item.judge_score.toFixed(2)})` : ""}
            </dd>
            {item.model ? (
              <>
                <dt className="text-subtle">model</dt>
                <dd>
                  {item.model}
                  {item.prompt_version ? ` · ${item.prompt_version}` : ""}
                </dd>
              </>
            ) : null}
            <dt className="text-subtle">{t("สร้าง", "Created")}</dt>
            <dd>{formatDate(item.created_at)}</dd>
          </dl>
          <section>
            <h3 className="mb-1 text-[11px] uppercase tracking-wide text-subtle">{t("การใช้ล่าสุด", "Recent uses")} · {detail.uses.length}</h3>
            {detail.uses.length === 0 ? <p className="text-subtle">{t("ยังไม่มี run ที่ใช้บทเรียนนี้", "No run has used this lesson yet")}</p> : null}
            <ul className="space-y-1">
              {detail.uses.map((u) => (
                <li key={`${u.run_id}-${u.created_at}`} data-testid="rb-use" className="num flex items-center gap-2 text-[12px]">
                  <span className="text-muted">{shortId(u.run_id)}</span>
                  <span className={u.outcome === "success" ? "text-done" : u.outcome === "failure" ? "text-error" : "text-subtle"}>
                    {u.outcome ?? t("รอผล", "pending")}
                  </span>
                  <span className="ml-auto text-subtle">{formatDate(u.created_at)}</span>
                </li>
              ))}
            </ul>
          </section>
          {item.status !== "retired" ? (
            <button
              type="button"
              data-testid="rb-retire"
              disabled={busy}
              onClick={() => void retire()}
              className="inline-flex items-center gap-1 rounded-md bg-layer-2 px-3 py-1 text-[12.5px] text-fg hover:bg-error/10 hover:text-error disabled:opacity-50"
            >
              <Archive className="size-3.5" aria-hidden /> {t("ปิดบทเรียนนี้ (retire)", "Retire this lesson")}
            </button>
          ) : null}
        </div>
      ) : null}
    </Drawer>
  );
}

export function ReasoningBank() {
  const t = useT();
  const [workflow, setWorkflow] = useState("");
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [items, setItems] = useState<ReasoningItemOut[] | null>(null);
  const [stats, setStats] = useState<ReasoningStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => window.clearTimeout(t);
  }, [q]);

  useEffect(() => {
    let cancelled = false;
    memoryApi
      .reasoning({ workflow, kind, q: debouncedQ, limit: 200 })
      .then((r) => {
        if (cancelled) return;
        setItems(r.items);
        setError(null);
      })
      .catch((err: unknown) => !cancelled && setError(errText(err)));
    return () => {
      cancelled = true;
    };
  }, [workflow, kind, debouncedQ, revision]);

  useEffect(() => {
    memoryApi
      .reasoningStats()
      .then(setStats)
      .catch(() => setStats(null));
  }, [revision]);

  return (
    <div data-testid="reasoning-bank" className="space-y-3">
      <p className="text-[12px] text-muted">
        {t(
          "บทเรียนที่ระบบกลั่นจาก run ที่สำเร็จและล้มเหลว (ไม่มีชื่อหุ้นในพอร์ต จำนวนเงิน หรือข้อมูลส่วนตัว) — run ใหม่ดึง 1–2 ข้อที่ใกล้ที่สุดไปใช้ และ success rate อัปเดตตามผลจริง",
          "Lessons distilled from successful and failed runs (no portfolio tickers, amounts or personal data) — each new run pulls the 1–2 closest ones, and the success rate updates with real outcomes",
        )}
      </p>
      <Stats stats={stats} />
      <div className="flex flex-wrap items-center gap-2 text-[12.5px]">
        <select aria-label="workflow" value={workflow} onChange={(e) => setWorkflow(e.target.value)} className="rounded-md border border-line bg-bg px-2 py-1 text-fg">
          {WORKFLOWS.map((w) => (
            <option key={w.id} value={w.id}>
              {w.label}
            </option>
          ))}
        </select>
        <select aria-label="kind" value={kind} onChange={(e) => setKind(e.target.value)} className="rounded-md border border-line bg-bg px-2 py-1 text-fg">
          {KINDS.map((k) => (
            <option key={k.id} value={k.id}>
              {k.label}
            </option>
          ))}
        </select>
        <label className="flex min-w-0 flex-1 items-center gap-1 rounded-md border border-line bg-bg px-2 py-1 sm:max-w-xs">
          <Search className="size-3.5 shrink-0 text-subtle" aria-hidden />
          <input
            aria-label={t("ค้นหาบทเรียน", "Search lessons")}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t("ค้นหา…", "Search…")}
            className="min-w-0 flex-1 bg-transparent text-fg outline-none placeholder:text-subtle"
          />
        </label>
      </div>
      {error ? <p className="text-[12.5px] text-error">{t("โหลด ReasoningBank ไม่ได้", "Could not load the ReasoningBank")}: {error}</p> : null}
      {!items && !error ? <div className="h-32 animate-pulse rounded-md bg-layer-1" /> : null}
      {items && items.length === 0 ? (
        <p data-testid="rb-empty" className="rounded-lg border border-dashed border-line p-6 text-center text-[12.5px] text-subtle">
          {t(
            "ยังไม่มีบทเรียน — จะเกิดหลังอนุมัติ/ปฏิเสธแผนของ advisor หรือ research ที่ตัดสินผลได้",
            "No lessons yet — they appear after you approve/reject an advisor plan or a research run with a clear outcome",
          )}
        </p>
      ) : null}
      {items && items.length ? (
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full min-w-[720px] text-left text-[12.5px]">
            <thead className="bg-layer-1 text-[11px] uppercase tracking-wide text-subtle">
              <tr>
                <th className="px-3 py-2 font-medium">{t("บทเรียน", "Lesson")}</th>
                <th className="px-2 py-2 font-medium">{t("ชนิด", "Kind")}</th>
                <th className="px-2 py-2 font-medium">workflow</th>
                <th className="px-2 py-2 font-medium">tags</th>
                <th className="px-2 py-2 text-right font-medium">{t("ใช้", "Uses")}</th>
                <th className="px-2 py-2 font-medium">success</th>
                <th className="px-3 py-2 font-medium">{t("สร้าง", "Created")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr
                  key={it.id}
                  data-testid="rb-row"
                  onClick={() => setOpen(it.id)}
                  className={clsx("cursor-pointer border-t border-line hover:bg-layer-2/50", it.status === "retired" && "opacity-60")}
                >
                  <td className="max-w-xs px-3 py-2">
                    <button type="button" onClick={() => setOpen(it.id)} className="block max-w-full truncate text-left font-medium text-fg hover:underline" title={it.title}>
                      {it.title}
                    </button>
                    <span className="block truncate text-[11.5px] text-muted" title={it.description}>
                      {it.description}
                    </span>
                  </td>
                  <td className="px-2 py-2">
                    <KindBadge kind={it.kind} />
                  </td>
                  <td className="px-2 py-2 text-muted">{it.tags.workflow}</td>
                  <td className="px-2 py-2">
                    <span className="flex flex-wrap gap-1">
                      {tagList(it.tags)
                        .slice(0, 3)
                        .map((t) => (
                          <Pill key={t}>{t}</Pill>
                        ))}
                    </span>
                  </td>
                  <td className="num px-2 py-2 text-right">{it.use_count}</td>
                  <td className="px-2 py-2">
                    <RatioBar value={it.success_rate} label="success rate" />
                  </td>
                  <td className="num px-3 py-2 text-subtle">{formatDate(it.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {open ? <DetailDrawer id={open} onClose={() => setOpen(null)} onRetired={() => setRevision((r) => r + 1)} /> : null}
    </div>
  );
}
