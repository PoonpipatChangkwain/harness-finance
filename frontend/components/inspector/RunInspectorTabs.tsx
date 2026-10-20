"use client";

/** Research inspector tabs, all reading the run selected in the transcript (ui-spec §4.2). */

import type { ReactNode } from "react";

import type { InspectorTab } from "@/components/shell/InspectorRegistry";
import { useNow } from "@/lib/hooks";
import { useT } from "@/lib/i18n";
import { isTerminal, type RunView } from "@/stores/run-reducer";
import { useRun } from "@/stores/run-store";
import { useUiStore } from "@/stores/ui-store";

import { ContextTab } from "./ContextTab";
import { MemoryTab } from "./MemoryTab";
import { ProvenanceTab } from "./ProvenanceTab";
import { ScratchpadTab } from "./ScratchpadTab";
import { TrajectoryTab } from "./TrajectoryTab";

function WithRun({ children }: { children: (view: RunView, now: number) => ReactNode }) {
  const t = useT();
  const runId = useUiStore((s) => s.selectedRunId);
  const view = useRun(runId);
  const now = useNow(view ? !isTerminal(view.status) : false, 1000);
  if (!runId || !view) {
    return <div className="p-4 text-[12.5px] text-subtle">{t("ยังไม่มี run · ส่งคำถามหรือคลิกที่ turn เพื่อดูรายละเอียด", "No run yet · send a question or click a turn to see details")}</div>;
  }
  return <>{children(view, now)}</>;
}

export const RESEARCH_TABS: InspectorTab[] = [
  { id: "trajectory", label: "Trajectory", render: () => <WithRun>{(v, now) => <TrajectoryTab view={v} now={now} />}</WithRun> },
  { id: "scratchpad", label: "Scratchpad", render: () => <WithRun>{(v) => <ScratchpadTab view={v} />}</WithRun> },
  { id: "provenance", label: "Provenance", render: () => <WithRun>{(v) => <ProvenanceTab view={v} />}</WithRun> },
  { id: "context", label: "Context", render: () => <WithRun>{(v) => <ContextTab view={v} />}</WithRun> },
  { id: "memory", label: "Memory", render: () => <WithRun>{(v) => <MemoryTab view={v} />}</WithRun> },
];
