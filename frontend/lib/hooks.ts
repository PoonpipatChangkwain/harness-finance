"use client";

import { useEffect, useState } from "react";

export type Breakpoint = "sm" | "md" | "lg" | "xl";

/** ui-spec §3: < 768 sheet sidebar · < 1024 inspector drawer · < 1280 icon sidebar. */
export function breakpointFor(width: number): Breakpoint {
  if (width < 768) return "sm";
  if (width < 1024) return "md";
  if (width < 1280) return "lg";
  return "xl";
}

export function useBreakpoint(): Breakpoint {
  const [bp, setBp] = useState<Breakpoint>("xl");
  useEffect(() => {
    const update = () => setBp(breakpointFor(window.innerWidth));
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  return bp;
}

/** Wall clock that ticks while `active` (for live durations). */
export function useNow(active: boolean, intervalMs = 500): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [active, intervalMs]);
  return now;
}
