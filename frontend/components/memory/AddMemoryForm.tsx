"use client";

/**
 * (P17) Add a memory by hand: category + one sentence → `POST /memory` (reconciled like a
 * chat fact, source `manual`) → show what changed ("เพิ่ม: …", "แทนที่: A → B", …).
 */

import { Plus } from "lucide-react";
import { useState, type FormEvent } from "react";

import { MEMORY_CATEGORIES, memoryApi, type MemoryCategory, type MemoryChangeOut } from "@/lib/memory-api";
import { tr, useT } from "@/lib/i18n";

export function changeText(c: MemoryChangeOut): string {
  const prev = c.previous?.text;
  switch (c.op) {
    case "ADD":
      return `${tr("เพิ่ม", "Added")}: ${c.item.text}`;
    case "UPDATE":
      return `${tr("อัปเดต", "Updated")}: ${prev ? `${prev} → ` : ""}${c.item.text}`;
    case "SUPERSEDE":
      return `${tr("แทนที่", "Replaced")}: ${prev ? `${prev} → ` : ""}${c.item.text}`;
    case "RETIRE":
      return `${tr("เลิกใช้", "Retired")}: ${c.item.text}`;
    case "NOOP":
      return `${tr("มีอยู่แล้ว", "Already known")}: ${c.item.text}`;
  }
}

export function AddMemoryForm({ onAdded }: { onAdded: () => void }) {
  const t = useT();
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
      <h2 className="text-[13.5px] font-semibold">{t("เพิ่มความจำ", "Add a memory")}</h2>
      <label className="flex flex-col gap-1 text-muted">
        {t("หมวด", "Category")}
        <select
          aria-label={t("หมวด", "Category")}
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
        {t("ข้อความ (1 ประโยค)", "Text (1 sentence)")}
        <textarea
          aria-label={t("ข้อความความจำ", "Memory text")}
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={2}
          placeholder={t("เช่น ผมไม่ลงทุนในหุ้นบุหรี่และแอลกอฮอล์", "e.g. I don't invest in tobacco or alcohol stocks")}
          className="resize-y rounded-md border border-line bg-bg px-2 py-1 text-[13px] text-fg outline-none placeholder:text-subtle focus:border-accent"
        />
      </label>
      <button
        type="submit"
        data-testid="add-memory-submit"
        disabled={busy || !text.trim()}
        className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1 text-bg disabled:opacity-40"
      >
        <Plus className="size-3.5" aria-hidden /> {busy ? t("กำลังบันทึก…", "Saving…") : t("บันทึก", "Save")}
      </button>
      {error ? <p className="text-error">{error}</p> : null}
      {changes ? (
        <div data-testid="add-memory-result" className="space-y-1 rounded-md bg-layer-2/60 p-2">
          <div className="text-[11px] uppercase tracking-wide text-subtle">{t("สิ่งที่เปลี่ยน", "Changes")}</div>
          {changes.length === 0 ? <p className="text-muted">{t("ไม่มีอะไรเปลี่ยน", "Nothing changed")}</p> : null}
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
