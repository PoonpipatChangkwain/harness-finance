"use client";

/**
 * Composer (ui-spec §4.1): auto-growing textarea (Enter sends, Shift+Enter
 * new line), scenario picker from /skills (unavailable ones greyed with the
 * reason), Send ↔ Stop while a run is live.
 */

import { clsx } from "clsx";
import { Send, Square } from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useRef, type KeyboardEvent, type ReactNode } from "react";

import type { SkillInfo } from "@/lib/api";

const CATEGORY_LABEL: Record<string, string> = {
  research: "Research",
  analysis: "Analysis",
  data_processing: "Data processing",
  reporting: "Reporting",
};

export function skillTitle(name: string): string {
  return name
    .split("-")
    .map((w) => (w.length <= 2 ? w.toUpperCase() : w[0]!.toUpperCase() + w.slice(1)))
    .join(" ");
}

export function ScenarioPicker({
  skills,
  value,
  onChange,
  disabled,
}: {
  skills: SkillInfo[];
  value: string | null;
  onChange: (skill: string | null) => void;
  disabled?: boolean;
}) {
  const groups = new Map<string, SkillInfo[]>();
  for (const s of skills) groups.set(s.category, [...(groups.get(s.category) ?? []), s]);
  const current = skills.find((s) => s.name === value);
  return (
    <select
      aria-label="Scenario"
      data-testid="scenario-picker"
      value={value ?? ""}
      disabled={disabled}
      title={current?.description ?? "Auto: ให้ agent เลือก scenario เอง"}
      onChange={(e) => onChange(e.target.value || null)}
      className="max-w-44 truncate rounded-md border border-line bg-layer-2 px-2 py-1 text-[12.5px] text-fg"
    >
      <option value="">Auto</option>
      {[...groups.entries()].map(([category, items]) => (
        <optgroup key={category} label={CATEGORY_LABEL[category] ?? category}>
          {items.map((s) => (
            <option key={s.name} value={s.name} disabled={!s.available} title={s.description}>
              {skillTitle(s.name)}
              {s.available ? "" : ` (ต้องมี ${s.missing.join(", ")})`}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

export interface ComposerHandle {
  focus: () => void;
}

export const Composer = forwardRef<
  ComposerHandle,
  {
    text: string;
    onText: (t: string) => void;
    skill: string | null;
    onSkill: (s: string | null) => void;
    skills: SkillInfo[];
    running: boolean;
    disabled?: boolean;
    disabledReason?: string;
    busy?: boolean;
    /** model picker placed next to the scenario picker */
    modelPicker?: ReactNode;
    /** shown under the input row (e.g. "set the API key first") */
    notice?: ReactNode;
    onSend: () => void;
    onStop: () => void;
  }
>(function Composer(props, ref) {
  const { text, onText, skill, onSkill, skills, running, disabled, disabledReason, busy, modelPicker, notice, onSend, onStop } =
    props;
  const area = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(ref, () => ({ focus: () => area.current?.focus() }), []);

  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [text]);

  const canSend = !running && !disabled && !busy && text.trim().length > 0;
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      if (canSend) onSend();
    }
  };

  return (
    <div className="rounded-lg border border-line bg-layer-1 p-2 focus-within:border-line-strong">
      <textarea
        ref={area}
        data-testid="composer-input"
        rows={1}
        value={text}
        onChange={(e) => onText(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder={skill ? `${skillTitle(skill)}: พิมพ์ symbol หรือคำถาม เช่น "AAPL"` : "ถามเรื่องหุ้น ตลาด หรือเศรษฐกิจ… (Enter ส่ง, Shift+Enter ขึ้นบรรทัด)"}
        className="block max-h-60 w-full resize-none bg-transparent px-1 py-1 text-[14px] text-fg outline-none placeholder:text-subtle"
      />
      <div className="mt-1 flex flex-wrap items-center gap-2">
        <ScenarioPicker skills={skills} value={skill} onChange={onSkill} disabled={running} />
        {modelPicker}
        {disabled && disabledReason ? <span className="truncate text-[12px] text-warning">{disabledReason}</span> : null}
        <span className="flex-1" />
        {running ? (
          <button
            type="button"
            data-testid="stop-button"
            onClick={onStop}
            className="inline-flex items-center gap-1.5 rounded-md bg-error/15 px-3 py-1 text-[13px] text-error hover:bg-error/25"
          >
            <Square className="size-3.5 fill-current" aria-hidden /> Stop
          </button>
        ) : (
          <button
            type="button"
            data-testid="send-button"
            onClick={onSend}
            disabled={!canSend}
            className={clsx(
              "inline-flex items-center gap-1.5 rounded-md px-3 py-1 text-[13px]",
              canSend ? "bg-accent text-bg hover:opacity-90" : "cursor-not-allowed bg-layer-2 text-subtle",
            )}
          >
            <Send className="size-3.5" aria-hidden /> Send
          </button>
        )}
      </div>
      {notice}
    </div>
  );
});
