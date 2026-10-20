"use client";

/**
 * Research Chat (phase-08 §8.5, ui-spec §4).
 * - no id: scenario cards; the first Send creates the session and replaces the URL
 * - with id: load session (messages + runs), render each turn from its run's
 *   events (replay from seq 0) and follow the run that is still live
 */

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApprovalPanel } from "@/components/conversation/ApprovalPanel";
import { Composer, skillTitle, type ComposerHandle } from "@/components/conversation/Composer";
import { Conversation, ConversationHeader } from "@/components/conversation/Conversation";
import { KeyWarning, ModelPicker, choiceProblem, useLlmProviders } from "@/components/conversation/ModelPicker";
import { PortfolioToggle } from "@/components/conversation/PortfolioToggle";
import type { TurnData } from "@/components/conversation/Turn";
import { RESEARCH_TABS } from "@/components/inspector/RunInspectorTabs";
import { useInspectorTabs } from "@/components/shell/InspectorRegistry";
import { api, ApiError, type SessionDetail, type SkillInfo } from "@/lib/api";
import { sendChatMessage } from "@/lib/chat";
import { useNow } from "@/lib/hooks";
import { tr, useT } from "@/lib/i18n";
import { isTerminal } from "@/stores/run-reducer";
import { usePortfolioStore } from "@/stores/portfolio-store";
import { useRun, useRunConn } from "@/stores/run-store";
import { useSessionStore } from "@/stores/session-store";
import { useUiStore } from "@/stores/ui-store";

import { ScenarioCards } from "./ScenarioCards";

export function ResearchView({ sessionId }: { sessionId: string | null }) {
  const t = useT();
  const router = useRouter();
  const search = useSearchParams();
  const reloadSessions = useSessionStore((s) => s.reload);
  const selectRun = useUiStore((s) => s.selectRun);
  const selectedRunId = useUiStore((s) => s.selectedRunId);
  const composerRef = useRef<ComposerHandle>(null);
  const llmChoice = useUiStore((s) => s.llmChoice);
  const setLlmChoice = useUiStore((s) => s.setLlmChoice);
  const llm = useLlmProviders();
  const portfolios = usePortfolioStore((s) => s.portfolios);
  const reloadPortfolios = usePortfolioStore((s) => s.reload);
  const [portfolioIds, setPortfolioIds] = useState<string[]>([]);
  const portfolioNames = useMemo(() => new Map(portfolios.map((p) => [p.id, p.name])), [portfolios]);

  const [detail, setDetail] = useState<SessionDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [localTurns, setLocalTurns] = useState<TurnData[]>([]);
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [text, setText] = useState(() => search?.get("prefill") ?? "");
  const [skill, setSkill] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [resumable, setResumable] = useState(false);

  useInspectorTabs(RESEARCH_TABS);

  useEffect(() => {
    api.skills().then(setSkills).catch(() => setSkills([]));
    void reloadPortfolios();
  }, [reloadPortfolios]);
  // a book deleted meanwhile is dropped from the selection
  useEffect(() => {
    setPortfolioIds((ids) => ids.filter((id) => portfolioNames.has(id)));
  }, [portfolioNames]);

  const loadDetail = useCallback(async () => {
    if (!sessionId) return;
    try {
      setDetail(await api.getSession(sessionId));
      setLoadError(null);
    } catch (err) {
      setLoadError(
        err instanceof ApiError && err.status === 404 ? tr("ไม่พบ session นี้", "Session not found") : String(err instanceof Error ? err.message : err),
      );
    }
  }, [sessionId]);

  useEffect(() => {
    setDetail(null);
    setLoadError(null);
    setLocalTurns((prev) => (sessionId ? prev : []));
    void loadDetail();
  }, [sessionId, loadDetail]);

  const turns = useMemo<TurnData[]>(() => {
    const saved: TurnData[] = (detail?.messages ?? [])
      .filter((m) => m.role === "user")
      .map((m) => ({
        key: m.id,
        text: m.content,
        skill: typeof m.meta?.skill === "string" ? m.meta.skill : null,
        runId: m.run_id,
        llm: typeof m.meta?.llm === "string" ? m.meta.llm : null,
        portfolios: Array.isArray(m.meta?.portfolio_ids)
          ? (m.meta.portfolio_ids as string[]).map((id) => portfolioNames.get(id) ?? id)
          : null,
      }));
    const known = new Set(saved.map((t) => t.runId));
    return [...saved, ...localTurns.filter((t) => !known.has(t.runId))];
  }, [detail, localTurns, portfolioNames]);

  const latestRunId = turns.at(-1)?.runId ?? null;
  const latest = useRun(latestRunId);
  const conn = useRunConn(latestRunId);
  const running = latest ? !isTerminal(latest.status) : latestRunId !== null && !detail;
  const now = useNow(running);

  // inspector follows the newest run unless the user picked one of this session's turns
  useEffect(() => {
    const ids = turns.map((t) => t.runId);
    if (latestRunId && !ids.includes(selectedRunId)) selectRun(latestRunId);
  }, [turns, latestRunId, selectedRunId, selectRun]);

  // a finished run may have set the session title / assistant message
  const latestDone = latest ? isTerminal(latest.status) : false;
  useEffect(() => {
    if (!latestDone) return;
    void loadDetail();
    void reloadSessions();
  }, [latestDone, loadDetail, reloadSessions]);

  const stop = useCallback(() => {
    if (latestRunId) void api.cancelRun(latestRunId).catch(() => {});
  }, [latestRunId]);

  // (P09) a run left waiting_approval/interrupted by a crash or a prior server run:
  // learned from GET /runs/{id} (there is no live SSE event for it) rather than the reducer.
  useEffect(() => {
    if (!latestRunId) {
      setResumable(false);
      return;
    }
    let cancelled = false;
    api
      .getRun(latestRunId)
      .then((r) => {
        if (!cancelled) setResumable(r.resumable);
      })
      .catch(() => {
        if (!cancelled) setResumable(false);
      });
    return () => {
      cancelled = true;
    };
  }, [latestRunId, latest?.status]);

  const resume = useCallback(async () => {
    if (!latestRunId) return;
    try {
      await api.resumeRun(latestRunId);
      setResumable(false);
    } catch (err) {
      setSendError(err instanceof Error ? err.message : String(err));
    }
  }, [latestRunId]);

  const pendingApproval = latest ? Object.values(latest.approvals).find((a) => a.outcome === "pending") : undefined;

  const resolveApproval = useCallback(
    async (outcome: "approved" | "rejected") => {
      if (!pendingApproval) return;
      try {
        await api.resolveApproval(pendingApproval.approvalId, { outcome });
      } catch (err) {
        setSendError(err instanceof Error ? err.message : String(err));
      }
    },
    [pendingApproval],
  );

  useEffect(() => {
    if (!running) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !useUiStore.getState().paletteOpen) stop();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [running, stop]);

  const send = async () => {
    const body = text.trim();
    if (!body || busy) return;
    setBusy(true);
    setSendError(null);
    try {
      let sid = sessionId;
      if (!sid) sid = (await api.createSession({ kind: "research" })).id;
      const { run_id } = await sendChatMessage(sid, { text: body, skill, llm: llmChoice, portfolio_ids: portfolioIds });
      const llmLabel = llmChoice ? `${llmChoice.provider}:${llmChoice.model}` : null;
      const used = portfolioIds.map((id) => portfolioNames.get(id) ?? id);
      setLocalTurns((prev) => [...prev, { key: run_id, text: body, skill, runId: run_id, llm: llmLabel, portfolios: used }]);
      setText("");
      selectRun(run_id);
      if (!sessionId) router.replace(`/research/${sid}`);
      void reloadSessions();
    } catch (err) {
      setSendError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const streamDown = running && (conn === "reconnecting" || conn === "connecting");
  const keyProblem = choiceProblem(llmChoice, llm.data?.providers);
  const title = detail?.session.title ?? (sessionId ? (loadError ?? "…") : "New research");

  return (
    <Conversation
      header={<ConversationHeader title={title} view={latest} now={now} resumable={resumable} onResume={resume} />}
      turns={turns}
      now={now}
      skillLabel={skillTitle}
      empty={
        sessionId ? (
          loadError ? <p className="text-error">{loadError}</p> : <p className="text-subtle">{t("กำลังโหลด…", "Loading…")}</p>
        ) : (
          <ScenarioCards
            skills={skills}
            onPick={(name, example) => {
              setSkill(name);
              setText(example);
              composerRef.current?.focus();
            }}
          />
        )
      }
      composer={
        <>
          {sendError ? <p className="mb-1 text-[12.5px] text-error">{t("ส่งไม่สำเร็จ", "Send failed")}: {sendError}</p> : null}
          {pendingApproval ? (
            <ApprovalPanel approval={pendingApproval} onResolve={resolveApproval} />
          ) : (
            <Composer
              ref={composerRef}
              text={text}
              onText={setText}
              skill={skill}
              onSkill={setSkill}
              skills={skills}
              running={running}
              busy={busy}
              disabled={streamDown || keyProblem !== null}
              disabledReason={streamDown ? t("กำลังเชื่อมต่อ stream…", "Connecting the stream…") : undefined}
              modelPicker={
                <>
                  <ModelPicker providers={llm.data} choice={llmChoice} onChange={setLlmChoice} disabled={running} />
                  <PortfolioToggle portfolios={portfolios} selected={portfolioIds} onChange={setPortfolioIds} disabled={running} />
                </>
              }
              notice={keyProblem ? <KeyWarning message={keyProblem} /> : null}
              onSend={send}
              onStop={stop}
            />
          )}
        </>
      }
    />
  );
}
