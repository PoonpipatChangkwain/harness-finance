"use client";

/** A reorderable list (drag, or ↑/↓ for keyboard users) — provider order / failover chain. */

import { clsx } from "clsx";
import { ArrowDown, ArrowUp, GripVertical, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useT } from "@/lib/i18n";

export function move<T>(items: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= items.length || to >= items.length) return items;
  const next = [...items];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item!);
  return next;
}

export function OrderList<T>({
  items,
  keyOf,
  render,
  onChange,
  onRemove,
  label,
}: {
  items: T[];
  keyOf: (item: T) => string;
  render: (item: T, index: number) => ReactNode;
  onChange: (items: T[]) => void;
  onRemove?: (index: number) => void;
  label: string;
}) {
  const t = useT();
  const [dragging, setDragging] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);
  return (
    <ol aria-label={label} className="space-y-1">
      {items.map((item, i) => (
        <li
          key={keyOf(item)}
          data-testid="order-item"
          data-key={keyOf(item)}
          draggable
          onDragStart={(e) => {
            setDragging(i);
            e.dataTransfer.effectAllowed = "move";
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(i);
          }}
          onDragLeave={() => setOver(null)}
          onDrop={(e) => {
            e.preventDefault();
            if (dragging !== null) onChange(move(items, dragging, i));
            setDragging(null);
            setOver(null);
          }}
          onDragEnd={() => {
            setDragging(null);
            setOver(null);
          }}
          className={clsx(
            "flex items-center gap-2 rounded-md border bg-layer-2/50 px-2 py-1 text-[12.5px]",
            over === i && dragging !== i ? "border-accent" : "border-line",
            dragging === i && "opacity-50",
          )}
        >
          <GripVertical className="size-3.5 shrink-0 cursor-grab text-subtle" aria-hidden />
          <span className="num w-4 text-subtle">{i + 1}</span>
          <span className="min-w-0 flex-1">{render(item, i)}</span>
          <button type="button" aria-label={t(`เลื่อน ${keyOf(item)} ขึ้น`, `Move ${keyOf(item)} up`)} disabled={i === 0} onClick={() => onChange(move(items, i, i - 1))} className="rounded-sm p-0.5 text-subtle hover:text-fg disabled:opacity-30">
            <ArrowUp className="size-3.5" aria-hidden />
          </button>
          <button
            type="button"
            aria-label={t(`เลื่อน ${keyOf(item)} ลง`, `Move ${keyOf(item)} down`)}
            disabled={i === items.length - 1}
            onClick={() => onChange(move(items, i, i + 1))}
            className="rounded-sm p-0.5 text-subtle hover:text-fg disabled:opacity-30"
          >
            <ArrowDown className="size-3.5" aria-hidden />
          </button>
          {onRemove ? (
            <button type="button" aria-label={t(`ลบ ${keyOf(item)}`, `Remove ${keyOf(item)}`)} onClick={() => onRemove(i)} className="rounded-sm p-0.5 text-subtle hover:text-error">
              <X className="size-3.5" aria-hidden />
            </button>
          ) : null}
        </li>
      ))}
    </ol>
  );
}
