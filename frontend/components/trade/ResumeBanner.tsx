"use client";

/** "ล้มที่ node X — Resume จาก checkpoint" for a failed/interrupted resumable run (phase-11 §11.2). */

import { RotateCcw } from "lucide-react";

export function ResumeBanner({
  status,
  resumable,
  node,
  busy = false,
  error,
  onResume,
}: {
  status: string;
  resumable: boolean;
  node: string | null;
  busy?: boolean;
  error?: string | null;
  onResume: () => void;
}) {
  if (!resumable || !["failed", "interrupted"].includes(status)) return null;
  return (
    <div data-testid="resume-banner" className="flex flex-wrap items-center gap-2 rounded-md border border-error/50 bg-error/10 px-3 py-2 text-[13px]">
      <span className="text-error">
        {status === "interrupted" ? "run ถูกขัดจังหวะ" : "run ล้ม"}
        {node ? (
          <>
            {" "}
            ที่ node <b className="num">{node}</b>
          </>
        ) : null}
      </span>
      <span className="text-muted">— ทำต่อจาก checkpoint ล่าสุด (node ที่เสร็จแล้วไม่ต้องรันใหม่)</span>
      <button
        type="button"
        data-testid="resume-button"
        disabled={busy}
        onClick={onResume}
        className="ml-auto inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1 text-[13px] text-bg disabled:opacity-50"
      >
        <RotateCcw className="size-3.5" aria-hidden /> Resume จาก checkpoint
      </button>
      {error ? <span className="w-full text-[12px] text-error">{error}</span> : null}
    </div>
  );
}
