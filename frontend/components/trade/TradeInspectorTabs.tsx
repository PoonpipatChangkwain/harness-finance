"use client";

/** Trade inspector tabs (ui-spec §5): Node · Debate · Scratchpad · Provenance · Trajectory · State. */

import type { ReactNode } from "react";

import { ProvenanceTab } from "@/components/inspector/ProvenanceTab";
import { ScratchpadTab } from "@/components/inspector/ScratchpadTab";
import { StateTab } from "@/components/inspector/StateTab";
import { TrajectoryTab } from "@/components/inspector/TrajectoryTab";
import type { InspectorTab } from "@/components/shell/InspectorRegistry";
import { useNow } from "@/lib/hooks";
import { useT } from "@/lib/i18n";
import { debateKindOf } from "@/lib/trade";
import type { TradeRunDetail } from "@/lib/trade-api";
import { isTerminal, type RunView } from "@/stores/run-reducer";
import { useRun } from "@/stores/run-store";
import { detailKey, useTradeDetail } from "@/stores/trade-store";
import { useUiStore } from "@/stores/ui-store";

import { DebateView } from "./DebateView";
import { NodePanel } from "./NodePanel";

function WithTradeRun({
  children,
}: {
  children: (view: RunView, detail: TradeRunDetail | undefined, now: number) => ReactNode;
}) {
  const t = useT();
  const runId = useUiStore((s) => s.selectedRunId);
  const view = useRun(runId);
  const detail = useTradeDetail(runId);
  const now = useNow(view ? !isTerminal(view.status) : false, 1000);
  if (!runId || !view) return <div className="p-4 text-[12.5px] text-subtle">{t("ยังไม่มี run · เลือก symbol แล้วกด Run", "No run yet · pick a symbol and press Run")}</div>;
  return <>{children(view, detail, now)}</>;
}

function NodeTab() {
  const node = useUiStore((s) => s.selectedNode);
  return <WithTradeRun>{(view, detail) => <NodePanel node={node} view={view} detail={detail} />}</WithTradeRun>;
}

function DebateTab() {
  const node = useUiStore((s) => s.selectedNode);
  const kind = debateKindOf(node);
  return (
    <WithTradeRun>
      {(_, detail) =>
        kind ? (
          <DebateView detail={detail} kind={kind} />
        ) : (
          <div className="space-y-4">
            <DebateView detail={detail} kind="invest" />
            <DebateView detail={detail} kind="risk" />
          </div>
        )
      }
    </WithTradeRun>
  );
}

export const TRADE_TABS: InspectorTab[] = [
  { id: "node", label: "Node", render: () => <NodeTab /> },
  { id: "debate", label: "Debate", render: () => <DebateTab /> },
  { id: "scratchpad", label: "Scratchpad", render: () => <WithTradeRun>{(v) => <ScratchpadTab view={v} />}</WithTradeRun> },
  { id: "provenance", label: "Provenance", render: () => <WithTradeRun>{(v) => <ProvenanceTab view={v} />}</WithTradeRun> },
  {
    id: "trajectory",
    label: "Trajectory",
    render: () => <WithTradeRun>{(v, _d, now) => <TrajectoryTab view={v} now={now} />}</WithTradeRun>,
  },
  {
    id: "state",
    label: "State",
    render: () => (
      <WithTradeRun>
        {(v, d) => (
          <StateTab
            runId={v.runId}
            refreshKey={detailKey(v)}
            fallback={(d?.run as { output?: { state?: unknown } } | undefined)?.output?.state}
          />
        )}
      </WithTradeRun>
    ),
  },
];
