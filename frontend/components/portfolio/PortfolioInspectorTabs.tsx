"use client";

/** Inspector tabs of /portfolio: AI Advisor, lineage of a clicked number, and the selected run. */

import type { InspectorTab } from "@/components/shell/InspectorRegistry";
import { Lineage } from "@/components/inspector/ProvenanceTab";
import { RESEARCH_TABS } from "@/components/inspector/RunInspectorTabs";
import { shortId } from "@/lib/format";
import { useUiStore } from "@/stores/ui-store";

import { AdvisorPanel } from "./AdvisorPanel";

function LineageTab() {
  const pid = useUiStore((s) => s.focusProvenanceId);
  if (!pid) {
    return <div className="p-4 text-[12.5px] text-subtle">คลิกตัวเลขที่มีเส้นประในหน้าพอร์ต เพื่อดูว่ามาจากข้อมูลและการคำนวณอะไร</div>;
  }
  return (
    <div data-testid="lineage-tab" className="p-3 text-[12px]">
      <div className="mb-2 text-[11px] uppercase tracking-wide text-subtle">
        Lineage · <span className="num normal-case">{shortId(pid)}</span>
      </div>
      <Lineage pid={pid} />
    </div>
  );
}

const runTab = (id: string) => RESEARCH_TABS.find((t) => t.id === id)!;

export const PORTFOLIO_TABS: InspectorTab[] = [
  { id: "advisor", label: "AI Advisor", render: () => <AdvisorPanel /> },
  { id: "provenance", label: "Lineage", render: () => <LineageTab /> },
  runTab("trajectory"),
  runTab("scratchpad"),
  runTab("memory"),
];
