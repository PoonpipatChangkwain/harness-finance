"use client";

/**
 * (P17) Toast "บันทึกบทเรียนลง ReasoningBank" when a run distils lessons
 * (`reasoning.distilled`). Only for runs watched while still live — replaying an old run
 * never pops it again. Disappears after 6 s or on ×.
 */

import { Lightbulb, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { isTerminal, type RunView } from "@/stores/run-reducer";

const SHOW_MS = 6000;

export function ReasoningToast({ view }: { view: RunView | undefined }) {
  const live = useRef(new Set<string>());
  const shown = useRef(new Set<string>());
  const [toast, setToast] = useState<{ runId: string; titles: string[] } | null>(null);

  const runId = view?.runId ?? null;
  const terminal = view ? isTerminal(view.status) : true;
  const count = view?.memory.distilled.length ?? 0;

  useEffect(() => {
    if (runId && !terminal) live.current.add(runId);
  }, [runId, terminal]);

  useEffect(() => {
    if (!view || !count || !live.current.has(view.runId) || shown.current.has(view.runId)) return;
    shown.current.add(view.runId);
    setToast({ runId: view.runId, titles: view.memory.distilled.map((d) => d.title) });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once per run, when lessons arrive
  }, [runId, count]);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), SHOW_MS);
    return () => window.clearTimeout(t);
  }, [toast]);

  if (!toast) return null;
  return (
    <div
      role="status"
      data-testid="reasoning-toast"
      className="fixed bottom-4 right-4 z-50 flex max-w-[min(360px,calc(100vw-32px))] items-start gap-2 rounded-lg border border-line-strong bg-layer-1 px-3 py-2 text-[12.5px] shadow-lg"
    >
      <Lightbulb className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="font-medium text-fg">บันทึกบทเรียนลง ReasoningBank</div>
        <ul className="mt-0.5 space-y-0.5 text-muted">
          {toast.titles.map((t) => (
            <li key={t} className="truncate" title={t}>
              {t}
            </li>
          ))}
        </ul>
        <Link href="/memory?tab=reasoning" className="text-[12px] text-accent hover:underline">
          ดูใน ReasoningBank
        </Link>
      </div>
      <button type="button" aria-label="ปิด" onClick={() => setToast(null)} className="rounded p-0.5 text-subtle hover:text-fg">
        <X className="size-3.5" aria-hidden />
      </button>
    </div>
  );
}
