import type { ReactNode } from "react";

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="num rounded-sm border border-line bg-layer-2 px-1 py-px text-[10px] text-muted">{children}</kbd>
  );
}
