"use client";

/**
 * AI Advisor (inspector tab of /portfolio, phase-13 §13.2): a `kind=portfolio` chat bound
 * to the open book, built from the same Turn / Composer as Research, plus 3 quick actions:
 * - "ตรวจความเสี่ยงพอร์ต" → skill `portfolio-risk-review` (no rebalance)
 * - "เสนอ rebalance"      → the `portfolio_advisor` workflow (ends at approval in the main column)
 * - "หาจุดอ่อน"           → skill `portfolio-weakness`
 * The chat session id per book is remembered in localStorage (lost storage = a new chat).
 * (P17) one line "ใช้โปรไฟล์ของคุณ: …" from /memory/profile, 5 Whys in the chat turns
 * (same DiscoveryCard / memory chips as Research), and a toast when a run distils lessons
 * into the ReasoningBank (after approve / reject).
 */

import { Brain, ChevronDown, ShieldCheck, Sparkles, Target } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Composer, skillTitle } from "@/components/conversation/Composer";
import { Turn, type TurnData } from "@/components/conversation/Turn";
import { ReasoningToast } from "@/components/memory/ReasoningToast";
import { api, ApiError, type SessionDetail, type SkillInfo } from "@/lib/api";
import { sendChatMessage } from "@/lib/chat";
import { useNow } from "@/lib/hooks";
import { memoryApi, profileSummary, type MemoryProfileOut } from "@/lib/memory-api";
import { portfolioApi, type AdvisorRun, type RiskProfile } from "@/lib/portfolio-api";
import { isTerminal } from "@/stores/run-reducer";
import { useRun } from "@/stores/run-store";
import { ALL, usePortfolioStore } from "@/stores/portfolio-store";
import { useUiStore } from "@/stores/ui-store";

const SESSION_KEY = (id: string) => `fh-pf-advisor-session:${id}`;
const RISK_LABEL: Record<RiskProfile, string> = { conservative: "ระมัดระวัง", balanced: "สมดุล", aggressive: "เชิงรุก" };

export const QUICK_ACTIONS = [
  { id: "risk", label: "ตรวจความเสี่ยงพอร์ต", skill: "portfolio-risk-review", text: "ตรวจความเสี่ยงพอร์ตนี้: ความกระจุกตัว, sector, correlation และตัวที่ขับความเสี่ยงหลัก" },
  { id: "weakness", label: "หาจุดอ่อน", skill: "portfolio-weakness", text: "หาจุดอ่อนของพอร์ตนี้: ตัวที่ contribution to risk สูงแต่ fundamentals อ่อน" },
] as const;

function readSession(id: string): string | null {
  try {
    return localStorage.getItem(SESSION_KEY(id));
  } catch {
    return null;
  }
}

function writeSession(id: string, sid: string | null) {
  try {
    if (sid) localStorage.setItem(SESSION_KEY(id), sid);
    else localStorage.removeItem(SESSION_KEY(id));
  } catch {
    // storage blocked: the chat is not remembered across reloads
  }
}

/** Chat turns + advisor runs of one book, oldest first. */
export function advisorTurns(detail: SessionDetail | null, runs: AdvisorRun[], local: (TurnData & { at: string })[]): TurnData[] {
  const items: (TurnData & { at: string })[] = [];
  for (const m of detail?.messages ?? []) {
    if (m.role !== "user") continue;
    items.push({ key: m.id, text: m.content, skill: typeof m.meta?.skill === "string" ? m.meta.skill : null, runId: m.run_id, at: m.created_at });
  }
  for (const r of runs) {
    items.push({
      key: r.run_id,
      text: `เสนอ rebalance · ${RISK_LABEL[r.risk_profile ?? "balanced"]}`,
      skill: "portfolio_advisor",
      runId: r.run_id,
      at: r.started_at,
    });
  }
  const known = new Set(items.map((i) => i.runId));
  items.push(...local.filter((l) => !known.has(l.runId)));
  return items
    .sort((a, b) => a.at.localeCompare(b.at))
    .map((t) => ({ key: t.key, text: t.text, skill: t.skill, runId: t.runId, llm: t.llm, portfolios: t.portfolios }));
}

export function AdvisorPanel() {
  const selectedId = usePortfolioStore((s) => s.selectedId);
  const portfolios = usePortfolioStore((s) => s.portfolios);
  const revision = usePortfolioStore((s) => s.revision);
  const setAdvisorRun = usePortfolioStore((s) => s.setAdvisorRun);
  const focusProposal = usePortfolioStore((s) => s.focusProposal);
  const selectRun = useUiStore((s) => s.selectRun);
  const llmChoice = useUiStore((s) => s.llmChoice);

  const book = portfolios.find((p) => p.id === selectedId) ?? null;
  const id = selectedId && selectedId !== ALL && book ? selectedId : null;

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [runs, setRuns] = useState<AdvisorRun[]>([]);
  const [local, setLocal] = useState<(TurnData & { at: string })[]>([]);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [text, setText] = useState("");
  const [skill, setSkill] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rebalanceOpen, setRebalanceOpen] = useState(false);
  const [risk, setRisk] = useState<RiskProfile>("balanced");
  const [goal, setGoal] = useState("");
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.skills().then(setSkills).catch(() => setSkills([]));
  }, []);

  const loadRuns = useCallback(() => {
    if (!id) return;
    portfolioApi
      .advisorRuns(id)
      .then((r) => setRuns([...r.runs].reverse()))
      .catch(() => setRuns([]));
  }, [id]);

  const loadDetail = useCallback(async (sid: string | null) => {
    if (!sid) return setDetail(null);
    try {
      setDetail(await api.getSession(sid));
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setSessionId(null);
        setDetail(null);
      }
    }
  }, []);

  useEffect(() => {
    setLocal([]);
    setError(null);
    const sid = id ? readSession(id) : null;
    setSessionId(sid);
    void loadDetail(sid);
    loadRuns();
  }, [id, loadDetail, loadRuns]);
  useEffect(loadRuns, [loadRuns, revision]);

  const turns = useMemo(() => advisorTurns(detail, runs, local), [detail, runs, local]);
  const latestRunId = turns.at(-1)?.runId ?? null;
  const latest = useRun(latestRunId);
  const running = latest ? !isTerminal(latest.status) : false;
  const now = useNow(running);
  const pending = latest ? Object.values(latest.approvals).some((a) => a.outcome === "pending") : false;

  useEffect(() => {
    if (latestRunId) selectRun(latestRunId);
  }, [latestRunId, selectRun]);
  useEffect(() => {
    if (!latest || !isTerminal(latest.status)) return;
    void loadDetail(sessionId);
    loadRuns();
  }, [latest?.status, latest, sessionId, loadDetail, loadRuns]);
  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: "end" });
  }, [turns.length]);

  const ensureSession = async (): Promise<string> => {
    if (sessionId) return sessionId;
    const created = await api.createSession({ kind: "portfolio", title: `AI Advisor · ${book?.name ?? id}`, portfolio_ids: [id!] });
    writeSession(id!, created.id);
    setSessionId(created.id);
    return created.id;
  };

  const ask = async (body: string, withSkill: string | null) => {
    if (!id || !body.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const sid = await ensureSession();
      const { run_id } = await sendChatMessage(sid, { text: body.trim(), skill: withSkill, llm: llmChoice, portfolio_ids: [id] });
      setLocal((l) => [...l, { key: run_id, text: body.trim(), skill: withSkill, runId: run_id, at: new Date().toISOString() }]);
      setText("");
      selectRun(run_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const rebalance = async () => {
    if (!id || busy) return;
    setBusy(true);
    setError(null);
    try {
      const { run_id } = await portfolioApi.advise(id, { risk_profile: risk, goal: goal.trim() || null, llm: llmChoice });
      setAdvisorRun(id, run_id);
      setLocal((l) => [
        ...l,
        { key: run_id, text: `เสนอ rebalance · ${RISK_LABEL[risk]}${goal.trim() ? ` · ${goal.trim()}` : ""}`, skill: "portfolio_advisor", runId: run_id, at: new Date().toISOString() },
      ]);
      setRebalanceOpen(false);
      selectRun(run_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (!id) {
    return (
      <div data-testid="advisor-panel" className="p-4 text-[12.5px] text-subtle">
        {selectedId === ALL || !selectedId ? "เลือกพอร์ตด้านซ้ายเพื่อคุยกับ AI Advisor" : "บันทึกพอร์ตก่อน แล้วค่อยให้ AI วิเคราะห์"}
      </div>
    );
  }

  return (
    <div data-testid="advisor-panel" className="flex h-full min-h-0 flex-col">
      <div className="space-y-1.5 border-b border-line p-2">
        <div className="text-[11.5px] text-muted">
          AI Advisor · <span className="text-fg">{book?.name}</span>
        </div>
        <ProfileLine refreshKey={`${latestRunId ?? ""}:${latest?.status ?? ""}`} />
        <div className="flex flex-wrap gap-1">
          <button
            type="button"
            data-testid="qa-risk"
            disabled={busy || running}
            onClick={() => void ask(QUICK_ACTIONS[0].text, QUICK_ACTIONS[0].skill)}
            className="inline-flex items-center gap-1 rounded-md bg-layer-2 px-2 py-1 text-[12px] hover:text-fg disabled:opacity-50"
          >
            <ShieldCheck className="size-3.5" aria-hidden /> {QUICK_ACTIONS[0].label}
          </button>
          <button
            type="button"
            data-testid="qa-rebalance"
            disabled={busy || running}
            aria-expanded={rebalanceOpen}
            onClick={() => setRebalanceOpen((v) => !v)}
            className="inline-flex items-center gap-1 rounded-md bg-accent-muted px-2 py-1 text-[12px] text-accent disabled:opacity-50"
          >
            <Target className="size-3.5" aria-hidden /> เสนอ rebalance <ChevronDown className="size-3" aria-hidden />
          </button>
          <button
            type="button"
            data-testid="qa-weakness"
            disabled={busy || running}
            onClick={() => void ask(QUICK_ACTIONS[1].text, QUICK_ACTIONS[1].skill)}
            className="inline-flex items-center gap-1 rounded-md bg-layer-2 px-2 py-1 text-[12px] hover:text-fg disabled:opacity-50"
          >
            <Sparkles className="size-3.5" aria-hidden /> {QUICK_ACTIONS[1].label}
          </button>
        </div>
        {rebalanceOpen ? (
          <div data-testid="rebalance-form" className="space-y-1.5 rounded-md border border-line bg-layer-2/40 p-2 text-[12px]">
            <label className="flex items-center gap-2 text-muted">
              ความเสี่ยง
              <select
                aria-label="risk profile"
                value={risk}
                onChange={(e) => setRisk(e.target.value as RiskProfile)}
                className="rounded-md border border-line bg-bg px-1.5 py-0.5 text-fg"
              >
                {(Object.keys(RISK_LABEL) as RiskProfile[]).map((k) => (
                  <option key={k} value={k}>
                    {RISK_LABEL[k]} ({k})
                  </option>
                ))}
              </select>
            </label>
            <input
              aria-label="goal"
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="เป้าหมาย (ไม่บังคับ) เช่น ลดความกระจุกตัวใน Energy"
              className="w-full rounded-md border border-line bg-bg px-2 py-1 text-fg outline-none focus:border-accent"
            />
            <button
              type="button"
              data-testid="start-rebalance"
              disabled={busy}
              onClick={() => void rebalance()}
              className="rounded-md bg-accent px-3 py-1 text-bg disabled:opacity-50"
            >
              เริ่มวิเคราะห์
            </button>
          </div>
        ) : null}
        {pending ? (
          <button type="button" onClick={focusProposal} className="w-full rounded-md bg-warning/15 px-2 py-1 text-left text-[12px] text-warning">
            ข้อเสนอรออนุมัติ → ไปที่ Rebalance proposal
          </button>
        ) : null}
        {error ? <p className="text-[12px] text-error">{error}</p> : null}
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-2 py-3">
        {turns.length === 0 ? (
          <p className="px-2 text-[12.5px] text-subtle">ถามเกี่ยวกับพอร์ตนี้ หรือกดปุ่มด่วนด้านบน — AI เห็นข้อมูล analytics ของพอร์ตพร้อม provenance</p>
        ) : (
          turns.map((t, i) => (
            <Turn
              key={t.key}
              turn={t}
              now={now}
              latest={i === turns.length - 1}
              skillLabel={(s) => (s === "portfolio_advisor" ? "Portfolio advisor" : skillTitle(s))}
            />
          ))
        )}
        <div ref={bottom} />
      </div>
      <div className="border-t border-line p-2">
        <Composer
          text={text}
          onText={setText}
          skill={skill}
          onSkill={setSkill}
          skills={skills}
          running={running && !pending}
          busy={busy}
          disabled={pending}
          disabledReason={pending ? "รออนุมัติ proposal ในหน้าหลัก" : undefined}
          onSend={() => void ask(text, skill)}
          onStop={() => latestRunId && void api.cancelRun(latestRunId).catch(() => {})}
        />
      </div>
      <ReasoningToast view={latest} />
    </div>
  );
}

/**
 * (P17) "ใช้โปรไฟล์ของคุณ: …" — first line of the profile the advisor really gets
 * (`GET /memory/profile`), refreshed after each run, with a link to /memory.
 */
export function ProfileLine({ refreshKey }: { refreshKey?: string }) {
  const [profile, setProfile] = useState<MemoryProfileOut | null | undefined>(undefined);
  useEffect(() => {
    let cancelled = false;
    memoryApi
      .profile()
      .then((p) => !cancelled && setProfile(p))
      .catch(() => !cancelled && setProfile(null));
    return () => {
      cancelled = true;
    };
  }, [refreshKey]);
  if (profile === undefined) return null;
  const summary = profileSummary(profile);
  return (
    <div data-testid="advisor-profile" className="flex min-w-0 items-center gap-1 text-[11.5px] text-muted">
      <Brain className="size-3 shrink-0 text-accent" aria-hidden />
      <span className="min-w-0 truncate" title={profile?.profile_text}>
        {summary ? (
          <>
            ใช้โปรไฟล์ของคุณ: <span className="text-fg">{summary}</span>
          </>
        ) : (
          "ยังไม่มีโปรไฟล์ในความจำ — AI จะจำจากที่คุยกัน"
        )}
      </span>
      <Link href="/memory" className="shrink-0 text-accent hover:underline">
        Memory
      </Link>
    </div>
  );
}
