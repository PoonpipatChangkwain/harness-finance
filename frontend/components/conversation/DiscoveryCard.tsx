"use client";

/**
 * (P17 §17.3) 5 Whys in the chat.
 * - `DiscoveryCard` under an answer that ends with a why-question (`answer.final.discovery`):
 *   progress "คำถาม n/5 เพื่อเข้าใจเป้าหมาย" and "ข้าม — วิเคราะห์เลย", which makes the NEXT
 *   message go out with `skip_discovery: true` (ui-store flag, a pill in the Composer).
 * - `GoalChip` when `discovery.assessed.root_goal_identified`: "เข้าใจเป้าหมายแล้ว: …".
 */

import { clsx } from "clsx";
import { FastForward, HelpCircle, Target } from "lucide-react";

import type { MemoryChangeEvent } from "@/lib/events";
import { useT } from "@/lib/i18n";
import type { DiscoveryView } from "@/stores/run-reducer";
import { useUiStore } from "@/stores/ui-store";

export function DiscoveryCard({ discovery, interactive = true }: { discovery: DiscoveryView; interactive?: boolean }) {
  const t = useT();
  const skip = useUiStore((s) => s.skipDiscovery);
  const setSkip = useUiStore((s) => s.setSkipDiscovery);
  if (!discovery.question) return null;
  const max = Math.max(1, discovery.max);
  const n = Math.min(max, Math.max(1, discovery.whysAsked));
  return (
    <div data-testid="discovery-card" className="space-y-2 rounded-lg border border-accent/30 bg-accent-muted/30 px-3 py-2 text-[12.5px]">
      <div className="flex flex-wrap items-center gap-2">
        <HelpCircle className="size-3.5 text-accent" aria-hidden />
        <span data-testid="discovery-progress" className="text-muted">
          {t(`คำถาม ${n}/${max} เพื่อเข้าใจเป้าหมาย`, `Question ${n}/${max} to understand your goal`)}
        </span>
        <span className="flex gap-0.5" aria-hidden>
          {Array.from({ length: max }, (_, i) => (
            <span key={i} className={clsx("h-1.5 w-4 rounded-full", i < n ? "bg-accent" : "bg-layer-2")} />
          ))}
        </span>
      </div>
      <p className="text-[13px] text-fg">{discovery.question}</p>
      {interactive ? (
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            data-testid="discovery-skip"
            aria-pressed={skip}
            onClick={() => setSkip(!skip)}
            className={clsx(
              "inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[12px]",
              skip ? "bg-accent text-bg" : "bg-layer-2 text-fg hover:bg-layer-1",
            )}
          >
            <FastForward className="size-3.5" aria-hidden /> {t("ข้าม — วิเคราะห์เลย", "Skip — analyze now")}
          </button>
          <span className="text-[11.5px] text-subtle">
            {skip
              ? t("ข้อความถัดไปจะวิเคราะห์ทันทีโดยไม่ถามต่อ", "The next message will be analyzed right away, no more questions")
              : t("ตอบในช่องพิมพ์ด้านล่าง หรือกดข้ามได้เสมอ", "Answer in the box below, or skip at any time")}
          </span>
        </div>
      ) : null}
    </div>
  );
}

/** Goal text for the chip: the assessed root goal, else a goal the turn just saved to memory. */
export function goalText(discovery: DiscoveryView, changes: MemoryChangeEvent[]): string | null {
  if (discovery.rootGoal) return discovery.rootGoal;
  const saved = changes.find((c) => c.category === "goal" && c.op !== "NOOP" && c.op !== "RETIRE");
  return saved?.text ?? null;
}

export function GoalChip({ discovery, changes }: { discovery: DiscoveryView; changes: MemoryChangeEvent[] }) {
  const t = useT();
  if (!discovery.rootGoalIdentified) return null;
  const text = goalText(discovery, changes);
  return (
    <span
      data-testid="goal-chip"
      title={text ?? undefined}
      className="inline-flex max-w-full items-center gap-1 rounded-full border border-done/40 bg-done/10 px-2 py-0.5 text-[11.5px] text-done"
    >
      <Target className="size-3 shrink-0" aria-hidden />
      <span className="min-w-0 truncate">{text ? `${t("เข้าใจเป้าหมายแล้ว", "Goal understood")}: ${text}` : t("เข้าใจเป้าหมายแล้ว", "Goal understood")}</span>
    </span>
  );
}
