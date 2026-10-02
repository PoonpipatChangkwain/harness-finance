"use client";

/**
 * Column resize handle (after dsh ui-layout `AppFrame.tsx` DragHandle):
 * pointer capture, rAF-throttled updates, width clamped by the caller.
 */

import { useRef } from "react";

export function DragHandle({
  width,
  onWidth,
  side,
  label,
}: {
  width: number;
  onWidth: (w: number) => void;
  /** which side of the handle the resized column is on */
  side: "left" | "right";
  label: string;
}) {
  const start = useRef<{ x: number; w: number } | null>(null);
  const frame = useRef<number | null>(null);

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "ArrowLeft") onWidth(width + (side === "right" ? 16 : -16));
        if (e.key === "ArrowRight") onWidth(width + (side === "right" ? -16 : 16));
      }}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        start.current = { x: e.clientX, w: width };
      }}
      onPointerMove={(e) => {
        const s = start.current;
        if (!s) return;
        const x = e.clientX;
        if (frame.current !== null) return;
        frame.current = requestAnimationFrame(() => {
          frame.current = null;
          const dx = x - s.x;
          onWidth(side === "left" ? s.w + dx : s.w - dx);
        });
      }}
      onPointerUp={(e) => {
        start.current = null;
        e.currentTarget.releasePointerCapture(e.pointerId);
      }}
      className="group relative z-10 w-1 shrink-0 cursor-col-resize bg-transparent outline-none"
    >
      <div className="absolute inset-y-0 left-0 w-px bg-line group-hover:bg-accent group-focus-visible:bg-accent" />
    </div>
  );
}
