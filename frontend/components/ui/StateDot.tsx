import { clsx } from "clsx";

/** Status dot used everywhere a state is shown (ui-spec §2, concept from dsh primitives). */
export type DotState = "ongoing" | "done" | "error" | "warning" | "idle";

const COLOR: Record<DotState, string> = {
  ongoing: "bg-ongoing fh-pulse",
  done: "bg-done",
  error: "bg-error",
  warning: "bg-warning",
  idle: "bg-idle",
};

export function StateDot({ state, className, label }: { state: DotState; className?: string; label?: string }) {
  return (
    <span
      role="img"
      aria-label={label ?? state}
      data-state={state}
      className={clsx("inline-block size-2 shrink-0 rounded-full", COLOR[state], className)}
    />
  );
}

/** Map run / tool / node statuses onto the five dot states. */
export function dotFor(status: string | null | undefined): DotState {
  switch (status) {
    case "running":
    case "pending":
    case "connecting":
    case "reconnecting":
      return "ongoing";
    case "ok":
    case "done":
    case "completed":
    case "open":
      return "done";
    case "error":
    case "failed":
    case "timeout":
    case "denied":
      return "error";
    case "partial":
    case "unavailable":
    case "limited":
    case "cancelled":
    case "interrupted":
      return "warning";
    default:
      return "idle";
  }
}
