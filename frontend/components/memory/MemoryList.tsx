"use client";

/**
 * (P17 §17.3) Everything the AI remembers about the user, grouped by category.
 * - "รอยืนยัน" (candidates) first: ยืนยัน / ไม่ใช่
 * - each item: text, source badge, confidence bar, date; แก้ (inline → PATCH = a new row
 *   that supersedes this one), เลิกใช้ (retire), ประวัติ (supersede chain drawer),
 *   ลืม (DELETE ?cascade=1 after a confirm dialog — the whole chain is gone for good)
 * - superseded / retired / rejected items only with "แสดงที่เลิกใช้แล้ว"
 * Every action reloads the list and the profile through `onChanged`.
 */

import { clsx } from "clsx";
import { Archive, Check, History, Pencil, ShieldAlert, Trash2, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import {
  MEMORY_CATEGORIES,
  SOURCE_LABEL,
  STATUS_LABEL,
  memoryApi,
  type MemoryItemOut,
} from "@/lib/memory-api";

import { Drawer, Pill, RatioBar, formatDate } from "./bits";
import { useT } from "@/lib/i18n";

const errText = (err: unknown) => (err instanceof Error ? err.message : String(err));

const STATUS_STYLE: Record<string, string> = {
  candidate: "bg-warning/15 text-warning",
  active: "bg-done/15 text-done",
  superseded: "bg-layer-2 text-subtle",
  retired: "bg-layer-2 text-subtle",
  rejected: "bg-error/10 text-error",
};

function ActionButton({
  onClick,
  children,
  testId,
  danger,
  disabled,
  title,
}: {
  onClick: () => void;
  children: ReactNode;
  testId: string;
  danger?: boolean;
  disabled?: boolean;
  title?: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={clsx(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px] disabled:opacity-50",
        danger ? "text-error hover:bg-error/10" : "text-muted hover:bg-layer-2 hover:text-fg",
      )}
    >
      {children}
    </button>
  );
}

export function ForgetDialog({ item, onCancel, onConfirm, busy }: { item: MemoryItemOut; onCancel: () => void; onConfirm: () => void; busy: boolean }) {
  const t = useT();
  return (
    <div role="dialog" aria-modal="true" aria-label={t("ลืมความจำ", "Forget memory")} className="fixed inset-0 z-50 grid place-items-center bg-black/50 p-4">
      <div className="w-full max-w-sm space-y-3 rounded-lg border border-line bg-layer-1 p-4 text-[13px]">
        <h2 className="flex items-center gap-1.5 text-[14px] font-semibold text-error">
          <ShieldAlert className="size-4" aria-hidden /> {t("ลบถาวรรวมประวัติ?", "Delete permanently, including history?")}
        </h2>
        <p className="rounded-md bg-layer-2/60 p-2 text-fg">{item.text}</p>
        <p className="text-[12px] text-muted">
          {t(
            "ข้อนี้และประวัติทั้งเส้น (ที่ถูกแทนที่ไปแล้ว) จะถูกลบออกจากความจำและการค้นหาทันที ย้อนกลับไม่ได้ — ถ้าแค่ไม่อยากให้ AI ใช้ ให้กด “เลิกใช้” แทน",
            "This item and its whole history (already superseded versions) are removed from memory and search right away. This cannot be undone — if you only want the AI to stop using it, press “Retire” instead.",
          )}
        </p>
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onCancel} className="rounded-md px-3 py-1 text-[12.5px] text-muted hover:bg-layer-2">
            {t("ยกเลิก", "Cancel")}
          </button>
          <button
            type="button"
            data-testid="forget-confirm"
            disabled={busy}
            onClick={onConfirm}
            className="rounded-md bg-error px-3 py-1 text-[12.5px] text-bg disabled:opacity-50"
          >
            {t("ลบถาวร", "Delete permanently")}
          </button>
        </div>
      </div>
    </div>
  );
}

export function HistoryDrawer({ item, onClose }: { item: MemoryItemOut; onClose: () => void }) {
  const t = useT();
  const [chain, setChain] = useState<MemoryItemOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    memoryApi
      .history(item.id)
      .then((r) => !cancelled && setChain(r.items))
      .catch((err: unknown) => !cancelled && setError(errText(err)));
    return () => {
      cancelled = true;
    };
  }, [item.id]);
  return (
    <Drawer label={t("ประวัติความจำ", "Memory history")} onClose={onClose}>
      <p className="mb-3 text-[11.5px] text-muted">
        {t(
          "เก่า → ใหม่ · ความจำไม่ถูกแก้ทับ: ทุกการเปลี่ยนคือแถวใหม่ที่แทนที่แถวเดิม",
          "Old → new · memories are never overwritten: every change is a new row that supersedes the previous one",
        )}
      </p>
      {error ? <p className="text-error">{t("โหลดประวัติไม่ได้", "Could not load history")}: {error}</p> : null}
      {!chain && !error ? <div className="h-24 animate-pulse rounded-md bg-layer-2" /> : null}
      <ol data-testid="history-chain" className="relative space-y-3 border-l border-line pl-4">
        {(chain ?? []).map((m) => (
          <li key={m.id} data-testid="history-item" className={clsx("relative", m.id === item.id && "font-medium")}>
            <span className={clsx("absolute -left-[21px] top-1.5 size-2.5 rounded-full border-2 border-layer-1", m.status === "active" ? "bg-done" : "bg-idle")} />
            <p className="break-words text-fg">{m.text}</p>
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[11px] text-subtle">
              <Pill className={STATUS_STYLE[m.status]}>{STATUS_LABEL[m.status] ?? m.status}</Pill>
              <span>{SOURCE_LABEL[m.source] ?? m.source}</span>
              <span className="num" data-testid="history-validity">
                {formatDate(m.valid_from)} → {m.valid_to ? formatDate(m.valid_to) : t("ปัจจุบัน", "now")}
              </span>
            </div>
          </li>
        ))}
      </ol>
    </Drawer>
  );
}

function MemoryItemRow({
  item,
  onChanged,
  onHistory,
  onForget,
}: {
  item: MemoryItemOut;
  onChanged: () => void;
  onHistory: (m: MemoryItemOut) => void;
  onForget: (m: MemoryItemOut) => void;
}) {
  const t = useT();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.text);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const live = item.status === "active" || item.status === "candidate";

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setEditing(false);
      onChanged();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <li
      data-testid="memory-item"
      data-status={item.status}
      className={clsx(
        "space-y-1.5 rounded-md border px-3 py-2",
        item.status === "candidate" ? "border-warning/40 bg-warning/5" : "border-line bg-layer-1",
        !live && "opacity-70",
      )}
    >
      {editing ? (
        <div className="space-y-1.5">
          <textarea
            aria-label={t("แก้ข้อความความจำ", "Edit memory text")}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={2}
            className="block w-full resize-y rounded-md border border-line bg-bg px-2 py-1 text-[13px] text-fg outline-none focus:border-accent"
          />
          <div className="flex gap-1.5">
            <button
              type="button"
              data-testid="memory-edit-save"
              disabled={busy || !draft.trim() || draft.trim() === item.text}
              onClick={() => void act(() => memoryApi.patch(item.id, { text: draft.trim() }))}
              className="rounded-md bg-accent px-2.5 py-0.5 text-[12px] text-bg disabled:opacity-40"
            >
              {t("บันทึก", "Save")}
            </button>
            <button
              type="button"
              onClick={() => {
                setEditing(false);
                setDraft(item.text);
              }}
              className="rounded-md px-2.5 py-0.5 text-[12px] text-muted hover:bg-layer-2"
            >
              {t("ยกเลิก", "Cancel")}
            </button>
          </div>
          <p className="text-[11px] text-subtle">
            {t("การแก้จะสร้างความจำใหม่แทนข้อเดิม (ข้อเดิมยังดูได้ในประวัติ)", "Editing creates a new memory that replaces this one (the old one stays in the history)")}
          </p>
        </div>
      ) : (
        <p className={clsx("break-words text-[13px] text-fg", !live && "line-through decoration-subtle")}>{item.text}</p>
      )}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-subtle">
        <Pill className="bg-accent-muted text-accent">{SOURCE_LABEL[item.source] ?? item.source}</Pill>
        {item.status !== "active" ? <Pill className={STATUS_STYLE[item.status]}>{STATUS_LABEL[item.status] ?? item.status}</Pill> : null}
        {item.sensitivity !== "normal" ? <Pill title={t("ไม่ถูกส่งไป ReasoningBank และถูกปิดบังใน log", "Never sent to the ReasoningBank and masked in logs")}>{t("ข้อมูลอ่อนไหว", "Sensitive")}</Pill> : null}
        <RatioBar value={item.confidence} label={t("ความมั่นใจ", "Confidence")} tone="accent" />
        <span className="num">
          {formatDate(item.valid_from)}
          {item.valid_to ? ` → ${formatDate(item.valid_to)}` : ""}
        </span>
      </div>
      {!editing ? (
        <div className="-ml-1.5 flex flex-wrap items-center gap-0.5">
          {item.status === "candidate" ? (
            <>
              <ActionButton testId="memory-confirm" disabled={busy} onClick={() => void act(() => memoryApi.confirm(item.id))}>
                <Check className="size-3.5 text-done" aria-hidden /> {t("ยืนยัน", "Confirm")}
              </ActionButton>
              <ActionButton testId="memory-reject" disabled={busy} onClick={() => void act(() => memoryApi.reject(item.id))} title={t("ไม่ใช่ความจริง — ไม่ต้องจำ", "Not true — don't remember it")}>
                <X className="size-3.5" aria-hidden /> {t("ไม่ใช่", "Not true")}
              </ActionButton>
            </>
          ) : null}
          {live ? (
            <ActionButton testId="memory-edit" disabled={busy} onClick={() => setEditing(true)}>
              <Pencil className="size-3.5" aria-hidden /> {t("แก้", "Edit")}
            </ActionButton>
          ) : null}
          {item.status === "active" ? (
            <ActionButton testId="memory-retire" disabled={busy} onClick={() => void act(() => memoryApi.retire(item.id))} title={t("AI จะไม่ใช้ข้อนี้อีก แต่ยังดูประวัติได้", "The AI stops using this, but the history stays")}>
              <Archive className="size-3.5" aria-hidden /> {t("เลิกใช้", "Retire")}
            </ActionButton>
          ) : null}
          <ActionButton testId="memory-history" onClick={() => onHistory(item)}>
            <History className="size-3.5" aria-hidden /> {t("ประวัติ", "History")}
          </ActionButton>
          <ActionButton testId="memory-forget" danger disabled={busy} onClick={() => onForget(item)}>
            <Trash2 className="size-3.5" aria-hidden /> {t("ลืม", "Forget")}
          </ActionButton>
        </div>
      ) : null}
      {error ? <p className="text-[12px] text-error">{error}</p> : null}
    </li>
  );
}

export function MemoryList({
  items,
  showInactive,
  onChanged,
}: {
  items: MemoryItemOut[];
  showInactive: boolean;
  onChanged: () => void;
}) {
  const t = useT();
  const [history, setHistory] = useState<MemoryItemOut | null>(null);
  const [forget, setForget] = useState<MemoryItemOut | null>(null);
  const [forgetting, setForgetting] = useState(false);
  const [forgetError, setForgetError] = useState<string | null>(null);

  const candidates = items.filter((m) => m.status === "candidate");
  const rest = items.filter((m) => m.status !== "candidate" && (showInactive || m.status === "active"));
  const byImportance = (a: MemoryItemOut, b: MemoryItemOut) => b.importance - a.importance;
  const groups = MEMORY_CATEGORIES.map((c) => ({ ...c, items: rest.filter((m) => m.category === c.id).sort(byImportance) }));
  const known = new Set<string>(MEMORY_CATEGORIES.map((c) => c.id));
  const other = rest.filter((m) => !known.has(m.category));
  if (other.length) groups.push({ id: "investment_style", label: t("อื่น ๆ", "Other"), items: other });

  const doForget = async () => {
    if (!forget) return;
    setForgetting(true);
    setForgetError(null);
    try {
      await memoryApi.forget(forget.id, true);
      setForget(null);
      onChanged();
    } catch (err) {
      setForgetError(errText(err));
    } finally {
      setForgetting(false);
    }
  };

  const row = (m: MemoryItemOut) => (
    <MemoryItemRow key={m.id} item={m} onChanged={onChanged} onHistory={setHistory} onForget={setForget} />
  );

  return (
    <div className="space-y-4">
      {forgetError ? <p className="text-[12px] text-error">{t("ลบไม่สำเร็จ", "Delete failed")}: {forgetError}</p> : null}
      {candidates.length ? (
        <section data-testid="memory-candidates" className="space-y-2 rounded-lg border border-warning/40 bg-warning/5 p-3">
          <h2 className="text-[13.5px] font-semibold text-warning">{t("รอยืนยัน", "Pending confirmation")} · {candidates.length}</h2>
          <p className="text-[11.5px] text-muted">
            {t(
              "AI อนุมานข้อเหล่านี้จากการคุย — ยังไม่ใช้เป็นข้อเท็จจริงจนกว่าคุณจะยืนยัน",
              "The AI inferred these from your chats — they are not used as facts until you confirm them",
            )}
          </p>
          <ul className="space-y-2">{candidates.map(row)}</ul>
        </section>
      ) : null}

      {groups
        .filter((g) => g.items.length)
        .map((g) => (
          <section key={g.label} data-testid="memory-group" data-category={g.id} className="space-y-2">
            <h2 className="text-[12px] font-semibold uppercase tracking-wide text-subtle">
              {g.label} · {g.items.length}
            </h2>
            <ul className="space-y-2">{g.items.map(row)}</ul>
          </section>
        ))}

      {!candidates.length && !rest.length ? (
        <p data-testid="memory-empty" className="rounded-lg border border-dashed border-line p-6 text-center text-[12.5px] text-subtle">
          {t(
            "ยังไม่มีความจำ — เล่าเรื่องการลงทุนของคุณในหน้า Research หรือเพิ่มเองด้วยฟอร์ม “เพิ่มความจำ”",
            "No memories yet — tell the AI about your investing on the Research page, or add one with the “Add a memory” form",
          )}
        </p>
      ) : null}

      {history ? <HistoryDrawer item={history} onClose={() => setHistory(null)} /> : null}
      {forget ? <ForgetDialog item={forget} busy={forgetting} onCancel={() => setForget(null)} onConfirm={() => void doForget()} /> : null}
    </div>
  );
}
