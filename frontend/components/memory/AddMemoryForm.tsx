"use client";

/**
 * (P17) Add a memory by hand: category + one sentence → `POST /memory` (reconciled like a
 * chat fact, source `manual`) → show what changed ("เพิ่ม: …", "แทนที่: A → B", …).
 */

import { Plus } from "lucide-react";
import { useState, type FormEvent } from "react";

import { MEMORY_CATEGORIES, memoryApi, type MemoryCategory, type MemoryChangeOut } from "@/lib/memory-api";

export function changeText(c: MemoryChangeOut): string {
  const prev = c.previous?.text;
  switch (c.op) {
    case "ADD":
      return `เพิ่ม: ${c.item.text}`;
    case "UPDATE":
      return prev ? `อัปเดต: ${prev} → ${c.item.text}` : `อัปเดต: ${c.item.text}`;
    case "SUPERSEDE":
      return prev ? `แทนที่: ${prev} → ${c.item.text}` : `แทนที่: ${c.item.text}`;
    case "RETIRE":
      return `เลิกใช้: ${c.item.text}`;
    case "NOOP":
      return `มีอยู่แล้ว: ${c.item.text}`;
  }
}

export function AddMemoryForm({ onAdded }: { onAdded: () => void }) {
  const [category, setCategory] = useState<MemoryCategory>("preference");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [changes, setChanges] = useState<MemoryChangeOut[] | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const out = await memoryApi.create({ category, text: text.trim() });
      setChanges(out.changes);
      setText("");
      onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form data-testid="add-memory" onSubmit={(e) => void submit(e)} className="space-y-2 rounded-lg border border-line bg-layer-1 p-3 text-[12.5px]">
      <h2 className="text-[13.5px] font-semibold">เพิ่มความจำ</h2>
      <label className="flex flex-col gap-1 text-muted">
        หมวด
        <select
          aria-label="หมวด"
          value={category}
          onChange={(e) => setCategory(e.target.value as MemoryCategory)}
          className="rounded-md border border-line bg-bg px-2 py-1 text-fg"
        >
          {MEMORY_CATEGORIES.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex flex-col gap-1 text-muted">
        ข้อความ (1 ประโยค)
        <textarea
          aria-label="ข้อความความจำ"
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          placeholder="เช่น ผมไม่ลงทุนในหุ้นบุหรี่และแอลกอฮอล์"
          className="resize-y rounded-md border border-line bg-bg px-2 py-1 text-[13px] text-fg outline-none placeholder:text-subtle focus:border-accent"
        />
      </label>
      <button
        type="submit"
        data-testid="add-memory-submit"
        disabled={busy || !text.trim()}
        className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1 text-bg disabled:opacity-40"
      >
        <Plus className="size-3.5" aria-hidden /> {busy ? "กำลังบันทึก…" : "บันทึก"}
      </button>
      {error ? <p className="text-error">{error}</p> : null}
      {changes ? (
        <div data-testid="add-memory-result" className="space-y-1 rounded-md bg-layer-2/60 p-2">
          <div className="text-[11px] uppercase tracking-wide text-subtle">สิ่งที่เปลี่ยน</div>
          {changes.length === 0 ? <p className="text-muted">ไม่มีอะไรเปลี่ยน</p> : null}
          <ul className="space-y-0.5">
            {changes.map((c) => (
              <li key={`${c.op}-${c.item.id}`} data-testid="add-memory-change" className="text-fg">
                {changeText(c)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </form>
  );
}
