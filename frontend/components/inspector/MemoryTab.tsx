"use client";

/**
 * (P17 §17.3) Inspector tab Memory: what the selected run recalled about the user, the
 * ReasoningBank lessons it used, the 5 Whys state, and what `learn` changed afterwards.
 * Everything comes from the run's events (`memory.recalled`, `discovery.assessed`,
 * `memory.updated`, `reasoning.distilled`) via the reducer — no extra request.
 */

import { clsx } from "clsx";
import Link from "next/link";
import type { ReactNode } from "react";

import { memoryChipText } from "@/components/conversation/MemoryChip";
import type { MemoryOp } from "@/lib/events";
import { shortId } from "@/lib/format";
import { CATEGORY_LABEL } from "@/lib/memory-api";
import type { RunView } from "@/stores/run-reducer";

const OP_STYLE: Record<MemoryOp, string> = {
  ADD: "bg-done/15 text-done",
  UPDATE: "bg-accent-muted text-accent",
  SUPERSEDE: "bg-accent-muted text-accent",
  RETIRE: "bg-warning/15 text-warning",
  NOOP: "bg-layer-2 text-subtle",
};

const ADVICE_MODE: Record<string, string> = { normal: "ปกติ", stabilise: "ให้ข้อเท็จจริงก่อน (stabilise)", educate: "อธิบายพื้นฐาน (educate)" };

function Section({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <section>
      <h3 className="mb-1 text-[11px] uppercase tracking-wide text-subtle">
        {title}
        {count !== undefined ? ` · ${count}` : ""}
      </h3>
      {children}
    </section>
  );
}

export function MemoryTab({ view }: { view: RunView }) {
  const { recalled, reasoning, changes, distilled, discovery } = view.memory;
  const empty = !recalled.length && !reasoning.length && !changes.length && !distilled.length && !discovery;
  return (
    <div data-testid="memory-tab" className="space-y-4 p-3 text-[12.5px]">
      {empty ? (
        <p className="text-subtle">run นี้ไม่ได้ใช้หรือบันทึก memory (หรือ workflow ยังไม่รองรับ)</p>
      ) : null}

      {discovery ? (
        <Section title="5 Whys">
          <dl className="num grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            <dt className="text-subtle">คำถาม</dt>
            <dd>
              {discovery.whysAsked}/{discovery.max}
            </dd>
            <dt className="text-subtle">เป้าหมาย</dt>
            <dd className={discovery.rootGoalIdentified ? "text-done" : "text-muted"}>
              {discovery.rootGoalIdentified ? (discovery.rootGoal ?? "เข้าใจแล้ว") : "ยังไม่ชัด"}
            </dd>
            {discovery.confidence !== null ? (
              <>
                <dt className="text-subtle">confidence</dt>
                <dd>{discovery.confidence.toFixed(2)}</dd>
              </>
            ) : null}
            {discovery.adviceMode ? (
              <>
                <dt className="text-subtle">advice mode</dt>
                <dd>{ADVICE_MODE[discovery.adviceMode] ?? discovery.adviceMode}</dd>
              </>
            ) : null}
            {discovery.stopReason ? (
              <>
                <dt className="text-subtle">stop reason</dt>
                <dd>{discovery.stopReason}</dd>
              </>
            ) : null}
            {discovery.question ?? discovery.nextQuestion ? (
              <>
                <dt className="text-subtle">ถาม</dt>
                <dd className="font-sans">{discovery.question ?? discovery.nextQuestion}</dd>
              </>
            ) : null}
          </dl>
        </Section>
      ) : null}

      <Section title="ความจำที่ดึงมาใช้" count={recalled.length}>
        {recalled.length === 0 ? <p className="text-subtle">ไม่มี</p> : null}
        <ul className="space-y-1">
          {recalled.map((m) => (
            <li key={m.id} data-testid="memory-recalled" className="rounded-md border border-line px-2 py-1">
              <span className="mr-1 rounded-sm bg-layer-2 px-1 text-[11px] text-muted">{CATEGORY_LABEL[m.category] ?? m.category}</span>
              {m.text}
              {m.status === "candidate" ? <span className="ml-1 text-[11px] text-warning">(ยังไม่ยืนยัน)</span> : null}
              <span className="num ml-1 text-[10.5px] text-subtle">{shortId(m.id)}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="บทเรียนจาก ReasoningBank ที่ใช้" count={reasoning.length}>
        {reasoning.length === 0 ? <p className="text-subtle">ไม่มี</p> : null}
        <ul className="space-y-1">
          {reasoning.map((r) => (
            <li key={r.id} data-testid="memory-reasoning" className="rounded-md border border-line px-2 py-1">
              {r.title}
              <span className="num ml-1 text-[10.5px] text-subtle">{shortId(r.id)}</span>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="สิ่งที่เปลี่ยนหลังเรียนรู้" count={changes.length}>
        {changes.length === 0 ? <p className="text-subtle">ไม่มี</p> : null}
        <ul className="space-y-1">
          {changes.map((c) => (
            <li key={`${c.op}-${c.id}`} data-testid="memory-change" className="flex items-start gap-1.5 rounded-md border border-line px-2 py-1">
              <span className={clsx("num shrink-0 rounded-sm px-1 text-[10.5px]", OP_STYLE[c.op])}>{c.op}</span>
              <span className="min-w-0">
                {memoryChipText(c) ?? c.text}
                <span className="ml-1 text-[11px] text-subtle">
                  {CATEGORY_LABEL[c.category] ?? c.category} · {c.status}
                </span>
              </span>
            </li>
          ))}
        </ul>
      </Section>

      {distilled.length ? (
        <Section title="บทเรียนใหม่ใน ReasoningBank" count={distilled.length}>
          <ul className="space-y-1">
            {distilled.map((d) => (
              <li key={d.id} data-testid="memory-distilled" className="rounded-md border border-line px-2 py-1">
                <span className="mr-1 rounded-sm bg-layer-2 px-1 text-[11px] text-muted">{d.kind}</span>
                {d.title}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Link href="/memory" className="inline-block text-[12px] text-accent hover:underline">
        เปิดหน้า Memory →
      </Link>
    </div>
  );
}
