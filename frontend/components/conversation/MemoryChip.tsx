"use client";

/**
 * (P17 §17.3) What the `learn` node changed in the user's memory after this turn
 * (`memory.updated`): "จำแล้ว: …" / "อัปเดต: A → B" / "เลิกใช้: …". The chip links to
 * /memory; "เลิกจำ" retires the item right away (POST /memory/{id}/retire).
 */

import { clsx } from "clsx";
import { Brain, Undo2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import type { MemoryChangeEvent } from "@/lib/events";
import { memoryApi } from "@/lib/memory-api";

export function memoryChipText(c: MemoryChangeEvent): string | null {
  switch (c.op) {
    case "ADD":
      return `จำแล้ว: ${c.text}`;
    case "UPDATE":
    case "SUPERSEDE":
      return c.previous_text ? `อัปเดต: ${c.previous_text} → ${c.text}` : `อัปเดต: ${c.text}`;
    case "RETIRE":
      return `เลิกใช้: ${c.text}`;
    default:
      return null;
  }
}

export function MemoryChip({ change }: { change: MemoryChangeEvent }) {
  const [state, setState] = useState<"idle" | "busy" | "retired" | "error">("idle");
  const text = memoryChipText(change);
  if (!text) return null;
  const canRetire = change.op !== "RETIRE" && state !== "retired";

  const retire = async () => {
    setState("busy");
    try {
      await memoryApi.retire(change.id);
      setState("retired");
    } catch {
      setState("error");
    }
  };

  return (
    <span
      data-testid="memory-chip"
      data-op={change.op}
      className={clsx(
        "inline-flex max-w-full items-center gap-1 rounded-full border px-2 py-0.5 text-[11.5px]",
        state === "retired" ? "border-line text-subtle line-through" : "border-accent/40 bg-accent-muted/40 text-accent",
      )}
    >
      <Brain className="size-3 shrink-0" aria-hidden />
      <Link href="/memory" title={`${text} — ดู/แก้ในหน้า Memory`} className="min-w-0 truncate hover:underline">
        {text}
      </Link>
      {change.status === "candidate" ? <span className="shrink-0 text-warning">(รอยืนยัน)</span> : null}
      {canRetire ? (
        <button
          type="button"
          data-testid="memory-chip-retire"
          disabled={state === "busy"}
          onClick={() => void retire()}
          title="เลิกจำข้อนี้ (ยังดูประวัติได้ในหน้า Memory)"
          className="ml-0.5 inline-flex shrink-0 items-center gap-0.5 rounded-full px-1 text-muted hover:bg-layer-2 hover:text-fg disabled:opacity-50"
        >
          <Undo2 className="size-3" aria-hidden /> เลิกจำ
        </button>
      ) : null}
      {state === "retired" ? <span className="shrink-0 no-underline">· เลิกจำแล้ว</span> : null}
      {state === "error" ? <span className="shrink-0 text-error">เลิกจำไม่สำเร็จ</span> : null}
    </span>
  );
}

/** Every change of one turn except NOOP. */
export function MemoryChips({ changes }: { changes: MemoryChangeEvent[] }) {
  const shown = changes.filter((c) => memoryChipText(c) !== null);
  if (!shown.length) return null;
  return (
    <div data-testid="memory-chips" className="flex flex-wrap gap-1.5">
      {shown.map((c) => (
        <MemoryChip key={`${c.op}-${c.id}`} change={c} />
      ))}
    </div>
  );
}
