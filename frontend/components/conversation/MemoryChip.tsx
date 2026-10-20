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
import { tr, useT } from "@/lib/i18n";
import { memoryApi } from "@/lib/memory-api";

export function memoryChipText(c: MemoryChangeEvent): string | null {
  switch (c.op) {
    case "ADD":
      return `${tr("จำแล้ว", "Remembered")}: ${c.text}`;
    case "UPDATE":
    case "SUPERSEDE":
      return c.previous_text ? `${tr("อัปเดต", "Updated")}: ${c.previous_text} → ${c.text}` : `${tr("อัปเดต", "Updated")}: ${c.text}`;
    case "RETIRE":
      return `${tr("เลิกใช้", "Retired")}: ${c.text}`;
    default:
      return null;
  }
}

export function MemoryChip({ change }: { change: MemoryChangeEvent }) {
  const t = useT();
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
      <Link href="/memory" title={`${text} — ${t("ดู/แก้ในหน้า Memory", "view/edit on the Memory page")}`} className="min-w-0 truncate hover:underline">
        {text}
      </Link>
      {change.status === "candidate" ? <span className="shrink-0 text-warning">({t("รอยืนยัน", "pending")})</span> : null}
      {canRetire ? (
        <button
          type="button"
          data-testid="memory-chip-retire"
          disabled={state === "busy"}
          onClick={() => void retire()}
          title={t("เลิกจำข้อนี้ (ยังดูประวัติได้ในหน้า Memory)", "Forget this (history stays on the Memory page)")}
          className="ml-0.5 inline-flex shrink-0 items-center gap-0.5 rounded-full px-1 text-muted hover:bg-layer-2 hover:text-fg disabled:opacity-50"
        >
          <Undo2 className="size-3" aria-hidden /> {t("เลิกจำ", "Forget")}
        </button>
      ) : null}
      {state === "retired" ? <span className="shrink-0 no-underline">· {t("เลิกจำแล้ว", "forgotten")}</span> : null}
      {state === "error" ? <span className="shrink-0 text-error">{t("เลิกจำไม่สำเร็จ", "Could not forget")}</span> : null}
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
