import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Composer } from "@/components/conversation/Composer";
import { DiscoveryCard, GoalChip, goalText } from "@/components/conversation/DiscoveryCard";
import { MemoryChip, MemoryChips, memoryChipText } from "@/components/conversation/MemoryChip";
import { MemoryTab } from "@/components/inspector/MemoryTab";
import { ReasoningToast } from "@/components/memory/ReasoningToast";
import { ProfileLine } from "@/components/portfolio/AdvisorPanel";
import { sendChatMessage } from "@/lib/chat";
import type { EventEnvelope, MemoryChangeEvent } from "@/lib/events";
import { replay } from "@/stores/run-reducer";
import { useUiStore } from "@/stores/ui-store";
import { loadDiscoveryRuns } from "@/tests/fixtures";

/** recorded 5 Whys session (research-discovery.jsonl): research + why 1/5 → ask-only 2/5 → identified */
const [turn1, , turn3] = loadDiscoveryRuns() as [EventEnvelope[], EventEnvelope[], EventEnvelope[]];
const askView = () => replay(turn1[0]!.run_id, turn1);
const identifiedView = (events = turn3) => replay(turn3[0]!.run_id, events);

interface Call {
  method: string;
  path: string;
  body: Record<string, unknown> | undefined;
}

function stubFetch(responses: Record<string, unknown> = {}) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(String(input));
      const method = (init.method ?? "GET").toUpperCase();
      const path = url.pathname.replace(/^\/api/, "");
      calls.push({ method, path, body: init.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : undefined });
      const data = responses[`${method} ${path}`];
      if (data === undefined) return new Response(JSON.stringify({ detail: "nope" }), { status: 500, headers: { "content-type": "application/json" } });
      return new Response(JSON.stringify(data), { status: 200, headers: { "content-type": "application/json" } });
    }),
  );
  return calls;
}

beforeEach(() => useUiStore.setState({ skipDiscovery: false }));
afterEach(() => vi.unstubAllGlobals());

const change = (over: Partial<MemoryChangeEvent>): MemoryChangeEvent => ({
  op: "ADD",
  id: "mem_1",
  category: "investment_style",
  text: "ชอบลงทุนระยะสั้น",
  status: "active",
  ...over,
});

describe("MemoryChip", () => {
  it("texts per op; NOOP has none", () => {
    expect(memoryChipText(change({}))).toBe("จำแล้ว: ชอบลงทุนระยะสั้น");
    expect(memoryChipText(change({ op: "SUPERSEDE", previous_text: "ระยะยาว", text: "ระยะสั้น" }))).toBe("อัปเดต: ระยะยาว → ระยะสั้น");
    expect(memoryChipText(change({ op: "UPDATE", previous_text: null }))).toBe("อัปเดต: ชอบลงทุนระยะสั้น");
    expect(memoryChipText(change({ op: "RETIRE", text: "ไม่ชอบหุ้นธนาคาร" }))).toBe("เลิกใช้: ไม่ชอบหุ้นธนาคาร");
    expect(memoryChipText(change({ op: "NOOP" }))).toBeNull();
  });

  it("links to /memory and 'เลิกจำ' posts retire", async () => {
    const calls = stubFetch({ "POST /memory/mem_1/retire": { id: "mem_1", status: "retired" } });
    render(<MemoryChip change={change({})} />);
    expect(screen.getByRole("link", { name: "จำแล้ว: ชอบลงทุนระยะสั้น" })).toHaveAttribute("href", "/memory");
    fireEvent.click(screen.getByTestId("memory-chip-retire"));
    expect(await screen.findByText("· เลิกจำแล้ว")).toBeInTheDocument();
    expect(calls).toEqual([{ method: "POST", path: "/memory/mem_1/retire", body: undefined }]);
    expect(screen.queryByTestId("memory-chip-retire")).not.toBeInTheDocument();
  });

  it("a RETIRE chip has no 'เลิกจำ'; a candidate is marked", () => {
    render(
      <MemoryChips
        changes={[
          change({ op: "RETIRE", id: "a", text: "x" }),
          change({ op: "ADD", id: "b", text: "y", status: "candidate" }),
          change({ op: "NOOP", id: "c" }),
        ]}
      />,
    );
    const chips = screen.getAllByTestId("memory-chip");
    expect(chips.map((c) => c.dataset.op)).toEqual(["RETIRE", "ADD"]);
    expect(screen.getAllByTestId("memory-chip-retire")).toHaveLength(1);
    expect(screen.getByText("(รอยืนยัน)")).toBeInTheDocument();
  });
});

describe("DiscoveryCard + skip flag", () => {
  const view = askView();

  it("shows progress n/5 and the question", () => {
    render(<DiscoveryCard discovery={view.memory.discovery!} />);
    expect(screen.getByTestId("discovery-progress")).toHaveTextContent("คำถาม 1/5 เพื่อเข้าใจเป้าหมาย");
    expect(screen.getByText("ถ้าได้ถือ PTT แล้วจะช่วยอะไรคุณ?")).toBeInTheDocument();
  });

  it("is hidden without a question; older turns have no skip button", () => {
    const { container } = render(<DiscoveryCard discovery={{ ...view.memory.discovery!, question: null }} />);
    expect(container).toBeEmptyDOMElement();
    render(<DiscoveryCard discovery={view.memory.discovery!} interactive={false} />);
    expect(screen.queryByTestId("discovery-skip")).not.toBeInTheDocument();
  });

  it("'ข้าม — วิเคราะห์เลย' shows a removable pill in the Composer and the next POST carries skip_discovery: true", async () => {
    const calls = stubFetch({ "POST /sessions/ses_1/messages": { run_id: "run_2", message_id: "msg_2" } });
    render(
      <>
        <DiscoveryCard discovery={view.memory.discovery!} />
        <Composer text="ตอบเลย" onText={() => {}} skill={null} onSkill={() => {}} skills={[]} running={false} onSend={() => {}} onStop={() => {}} />
      </>,
    );
    expect(screen.queryByTestId("skip-discovery-pill")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /ข้าม — วิเคราะห์เลย/ }));
    expect(screen.getByTestId("skip-discovery-pill")).toHaveTextContent("ข้ามคำถาม");

    // × removes it, clicking again sets it back
    fireEvent.click(screen.getByTestId("skip-discovery-clear"));
    expect(screen.queryByTestId("skip-discovery-pill")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("discovery-skip"));

    await act(async () => {
      await sendChatMessage("ses_1", { text: "ตอบเลย", skill: null, llm: null, portfolio_ids: [] });
    });
    expect(calls[0]!.body).toEqual({ text: "ตอบเลย", skill: null, llm: null, portfolio_ids: [], skip_discovery: true });
    // one-shot: the flag and the pill are gone; the following message goes without skipping
    expect(screen.queryByTestId("skip-discovery-pill")).not.toBeInTheDocument();
    await act(async () => {
      await sendChatMessage("ses_1", { text: "ต่อ" });
    });
    expect(calls[1]!.body).toMatchObject({ text: "ต่อ", skip_discovery: false });
  });

  it("a failed send keeps the skip flag for the retry", async () => {
    stubFetch({});
    useUiStore.setState({ skipDiscovery: true });
    await expect(sendChatMessage("ses_1", { text: "x" })).rejects.toThrow();
    expect(useUiStore.getState().skipDiscovery).toBe(true);
  });
});

describe("GoalChip", () => {
  it("'เข้าใจเป้าหมายแล้ว: …' from discovery.assessed.root_goal", () => {
    const v = identifiedView();
    render(<GoalChip discovery={v.memory.discovery!} changes={v.memory.changes} />);
    expect(screen.getByTestId("goal-chip")).toHaveTextContent("เข้าใจเป้าหมายแล้ว: เก็บเงินดาวน์บ้านภายในปี 2028");
  });

  it("falls back to the goal memory saved by the turn; hidden until identified", () => {
    // the same turn without root_goal in discovery.assessed
    const noGoal = turn3.map((e) =>
      e.type === "discovery.assessed" ? ({ ...e, payload: { ...e.payload, root_goal: null } } as EventEnvelope) : e,
    );
    const v = identifiedView(noGoal);
    expect(goalText(v.memory.discovery!, v.memory.changes)).toBe("ผู้ใช้เก็บเงินดาวน์บ้านภายในปี 2028");
    const ask = askView();
    const { container } = render(<GoalChip discovery={ask.memory.discovery!} changes={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("inspector tab Memory", () => {
  it("renders recalled items and the reasoning used (turn 1)", () => {
    render(<MemoryTab view={askView()} />);
    expect(screen.getAllByTestId("memory-recalled").map((li) => li.textContent)).toEqual([
      expect.stringContaining("ความชอบผู้ใช้ชอบหุ้นปันผลอย่าง PTT"),
    ]);
    expect(screen.getByTestId("memory-reasoning")).toHaveTextContent("Answer with data first, then ask why");
    expect(screen.queryAllByTestId("memory-change")).toHaveLength(0);
    expect(screen.getByText("1/5")).toBeInTheDocument();
  });

  it("renders changes after learn and distilled lessons (turn 3)", () => {
    render(<MemoryTab view={identifiedView()} />);
    expect(screen.getAllByTestId("memory-change")).toHaveLength(1);
    expect(screen.getByText("จำแล้ว: ผู้ใช้เก็บเงินดาวน์บ้านภายในปี 2028")).toBeInTheDocument();
    expect(screen.getByTestId("memory-distilled")).toHaveTextContent("Tie a single-stock idea to the user's dated goal");
    expect(screen.getByText("เก็บเงินดาวน์บ้านภายในปี 2028")).toBeInTheDocument();
  });

  it("marks a recalled candidate as not yet confirmed", () => {
    const candidate = turn1.map((e) =>
      e.type === "memory.recalled"
        ? ({ ...e, payload: { ...e.payload, items: e.payload.items.map((i) => ({ ...i, status: "candidate" })) } } as EventEnvelope)
        : e,
    );
    render(<MemoryTab view={replay(turn1[0]!.run_id, candidate)} />);
    expect(screen.getByTestId("memory-recalled")).toHaveTextContent("(ยังไม่ยืนยัน)");
  });

  it("empty run says so", () => {
    render(<MemoryTab view={replay("r", [])} />);
    expect(screen.getByText(/ไม่ได้ใช้หรือบันทึก memory/)).toBeInTheDocument();
  });
});

describe("AdvisorPanel pieces", () => {
  it("ProfileLine shows the first line of /memory/profile and links to /memory", async () => {
    stubFetch({
      "GET /memory/profile": { profile_text: "## INVESTOR PROFILE\n- ชอบลงทุนระยะยาว รับความเสี่ยงปานกลาง\n- เป้าหมาย: คอนโด 2028", token_estimate: 30, items_used: [], updated_at: null },
    });
    render(<ProfileLine />);
    expect(await screen.findByTestId("advisor-profile")).toHaveTextContent("ใช้โปรไฟล์ของคุณ: ชอบลงทุนระยะยาว รับความเสี่ยงปานกลาง");
    expect(screen.getByRole("link", { name: "Memory" })).toHaveAttribute("href", "/memory");
  });

  it("toast 'บันทึกบทเรียนลง ReasoningBank' only for a run watched live", () => {
    const live = identifiedView(turn3.slice(0, 3));
    const { rerender } = render(<ReasoningToast view={live} />);
    expect(screen.queryByTestId("reasoning-toast")).not.toBeInTheDocument();
    rerender(<ReasoningToast view={identifiedView()} />);
    expect(screen.getByTestId("reasoning-toast")).toHaveTextContent("บันทึกบทเรียนลง ReasoningBank");
    expect(screen.getByText("Tie a single-stock idea to the user's dated goal")).toBeInTheDocument();
  });

  it("no toast when an already finished run is replayed", async () => {
    render(<ReasoningToast view={identifiedView()} />);
    await waitFor(() => expect(screen.queryByTestId("reasoning-toast")).not.toBeInTheDocument());
  });
});
