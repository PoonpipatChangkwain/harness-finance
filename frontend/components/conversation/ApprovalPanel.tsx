"use client";

/**
 * Approval takeover of the composer (after dsh ui-approval `ApprovalPanel.tsx`):
 * Enter = approve, Esc = reject. Built in P08, used by the trade workflow in P09.
 */

import { Check, X } from "lucide-react";
import { useEffect } from "react";

import { JsonView } from "@/components/ui/JsonView";
import { Kbd } from "@/components/ui/Kbd";
import type { ApprovalView } from "@/stores/run-reducer";
import { useT } from "@/lib/i18n";

export function ApprovalPanel({
  approval,
  onResolve,
}: {
  approval: ApprovalView;
  onResolve: (outcome: "approved" | "rejected") => void;
}) {
  const t = useT();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (e.key === "Enter") onResolve("approved");
      if (e.key === "Escape") onResolve("rejected");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onResolve]);

  return (
    <div data-testid="approval-panel" className="space-y-2 rounded-lg border border-warning/60 bg-layer-1 p-3">
      <div className="text-[13px] font-medium text-warning">{t("ต้องอนุมัติ", "Approval required")}: {approval.title}</div>
      <JsonView data={approval.proposal} />
      {approval.policyResults.length ? (
        <ul className="space-y-0.5 text-[12.5px]">
          {approval.policyResults.map((r, i) => (
            <li key={i} className={r.passed ? "text-done" : "text-error"}>
              {r.passed ? "✓" : "✗"} {String(r.name ?? r.rule ?? `rule ${i + 1}`)}
              {r.detail ? <span className="text-muted"> · {String(r.detail)}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => onResolve("approved")}
          className="inline-flex items-center gap-1 rounded-md bg-done/20 px-3 py-1 text-[13px] text-done"
        >
          <Check className="size-3.5" /> Approve <Kbd>Enter</Kbd>
        </button>
        <button
          type="button"
          onClick={() => onResolve("rejected")}
          className="inline-flex items-center gap-1 rounded-md bg-error/15 px-3 py-1 text-[13px] text-error"
        >
          <X className="size-3.5" /> Reject <Kbd>Esc</Kbd>
        </button>
      </div>
    </div>
  );
}
