import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { Answer, markAnswer, stripRawMarkers } from "@/components/conversation/Answer";
import { CitationChip } from "@/components/conversation/CitationChip";
import { ContextMeter, contextLevel, contextPercent } from "@/components/conversation/ContextMeter";
import { CompactionItem } from "@/components/conversation/ProcessRows";
import { ToolCallTree, ToolRow } from "@/components/conversation/ToolCallTree";
import type { ProvenanceRecord } from "@/lib/api";
import { replay, type CallView } from "@/stores/run-reducer";
import { useUiStore } from "@/stores/ui-store";
import { loadFixture } from "@/tests/fixtures";

function call(overrides: Partial<CallView> = {}): CallView {
  return {
    callId: "call_1",
    tool: "get_prices",
    category: "data",
    args: { symbol: "PTT", range: "1y" },
    readOnly: true,
    batchId: "batch_1",
    node: "retrieve_analyze",
    parentCallId: null,
    seq: 1,
    startedAt: 0,
    finishedAt: null,
    status: "running",
    progress: null,
    summary: null,
    preview: null,
    truncated: false,
    error: null,
    provenanceIds: [],
    durationMs: null,
    children: [],
    ...overrides,
  };
}

describe("ToolRow", () => {
  it("shows progress text with shimmer while running", () => {
    render(<ToolRow call={call({ progress: "fetching 250 bars" })} runId="r" />);
    const row = screen.getByTestId("tool-row");
    expect(row.dataset.status).toBe("running");
    const text = screen.getByText("fetching 250 bars");
    expect(text.className).toContain("fh-shimmer");
    expect(screen.getByRole("img", { name: "running" }).dataset.state).toBe("ongoing");
  });

  it("shows summary, green dot and duration when done", () => {
    render(<ToolRow call={call({ status: "ok", summary: "PTT.BK · 1y · 250 bars", durationMs: 812, finishedAt: 1 })} runId="r" />);
    expect(screen.getByText("PTT.BK · 1y · 250 bars")).toBeInTheDocument();
    expect(screen.getByText("812ms")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "ok" }).dataset.state).toBe("done");
  });

  it("replaces the summary with the first error line and a red dot", () => {
    render(
      <ToolRow
        call={call({ status: "error", summary: "x", error: { type: "RuntimeError", message: "boom x\ntraceback…", retryable: false } })}
        runId="r"
      />,
    );
    expect(screen.getByText("boom x")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "error" }).dataset.state).toBe("error");
  });

  it("expands to arguments, output, provenance and meta", () => {
    render(
      <ToolRow
        call={call({ status: "ok", summary: "ok", preview: '{"close": 34.5}', provenanceIds: ["prov_01ABCDEFGHJKMNPQRSTVWXYZ12"] })}
        runId="r"
      />,
    );
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    expect(screen.getByText("Arguments")).toBeInTheDocument();
    expect(screen.getByText("Output")).toBeInTheDocument();
    expect(screen.getByText('{"close": 34.5}')).toBeInTheDocument();
    expect(screen.getByText(/Provenance · 1/)).toBeInTheDocument();
    expect(screen.getByText("read-only")).toBeInTheDocument();
  });

  it("joins a concurrent batch and nests subcalls (fixture)", () => {
    const events = loadFixture("concurrent-subcall");
    const view = replay(events[0]!.run_id, events);
    const batch = view.items.find((it) => it.kind === "batch" && it.callIds.length === 3);
    if (!batch || batch.kind !== "batch") throw new Error("no batch");
    render(<ToolCallTree callIds={batch.callIds} calls={view.calls} runId={view.runId} />);
    expect(screen.getByTestId("tool-batch").dataset.concurrent).toBe("true");
    expect(screen.getAllByTestId("tool-row")).toHaveLength(4); // 3 roots + 1 subcall
    expect(screen.getByText("fake_prices")).toBeInTheDocument();
  });
});

describe("CompactionItem", () => {
  it("full compaction expands to its summary", () => {
    render(
      <CompactionItem
        item={{ kind: "compaction", seq: 1, phase: "end", preTokens: 38_200, postTokens: 6_100, summary: "## Summary\n- kept PTT", cleared: null, kept: null }}
      />,
    );
    expect(screen.getByText(/Context compacted/)).toBeInTheDocument();
    expect(screen.getByText("38.2k → 6.1k")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("kept PTT")).toBeInTheDocument();
  });

  it("microcompact is a faint single line", () => {
    render(<CompactionItem item={{ kind: "microcompact", seq: 2, cleared: 6, tokensSaved: 3226 }} />);
    expect(screen.getByTestId("compaction").dataset.kind).toBe("micro");
    expect(screen.getByText("Cleared 6 old tool results (−3.2k tokens)")).toBeInTheDocument();
  });

  it("fallback_clear is amber", () => {
    render(
      <CompactionItem item={{ kind: "compaction", seq: 3, phase: "fallback_clear", preTokens: 1, postTokens: null, summary: null, cleared: 12, kept: 5 }} />,
    );
    const el = screen.getByTestId("compaction");
    expect(el.dataset.kind).toBe("fallback");
    expect(el.className).toContain("text-warning");
  });
});

describe("CitationChip", () => {
  beforeEach(() => useUiStore.setState({ focusProvenanceId: null, inspectorTab: "trajectory", selectedRunId: null }));

  const record: ProvenanceRecord = {
    id: "prov_1",
    kind: "data",
    value: 34.5,
    unit: "THB",
    source: "yfinance",
    tool: "get_quote",
    calculation: null,
    params: {},
    inputs: [],
    agent: null,
    run_id: "run_1",
    as_of: "2026-09-24",
    fetched_at: "2026-09-24T10:00:00Z",
    url: null,
  };

  it("hover shows the provenance popover", async () => {
    render(<CitationChip marker={1} provenanceId="prov_1" runId="run_1" load={async () => record} />);
    fireEvent.mouseEnter(screen.getByTestId("citation-chip").parentElement!);
    const pop = await screen.findByTestId("citation-popover");
    await waitFor(() => expect(pop).toHaveTextContent("34.5 THB"));
    expect(pop).toHaveTextContent("data · yfinance · get_quote");
    expect(pop).toHaveTextContent("as of 2026-09-24");
  });

  it("click opens the Provenance tab on that record", () => {
    render(<CitationChip marker={2} provenanceId="prov_2" runId="run_9" load={async () => null} />);
    act(() => fireEvent.click(screen.getByTestId("citation-chip")));
    const s = useUiStore.getState();
    expect([s.inspectorTab, s.focusProvenanceId, s.selectedRunId, s.inspectorOpen]).toEqual(["provenance", "prov_2", "run_9", true]);
  });
});

describe("ContextMeter", () => {
  it.each([
    [10_000, 128_000, 8, "normal"],
    [80_000, 128_000, 63, "warning"],
    [108_800, 128_000, 85, "warning"],
    [110_000, 128_000, 86, "danger"],
    [500_000, 128_000, 100, "danger"],
  ])("%i / %i → %i%% %s", (used, window, percent, level) => {
    expect(contextPercent(used, window)).toBe(percent);
    expect(contextLevel(percent)).toBe(level);
    render(<ContextMeter used={used} window={window} />);
    const meter = screen.getByTestId("context-meter");
    expect(meter.dataset.level).toBe(level);
    expect(meter).toHaveTextContent(`${percent}%`);
  });
});

describe("Answer", () => {
  it("turns markers into chips and underlines unverified numbers", () => {
    render(
      <Answer
        markdown={"| a | b |\n|---|---|\n| ปิด 34.5 [1] | 99.9 |\n\nRSI 55.2 [2] และ [3]"}
        citations={[
          { marker: 1, provenance_id: "prov_a" },
          { marker: 2, provenance_id: "prov_b" },
        ]}
        unverified={["99.9"]}
        runId="r"
      />,
    );
    expect(screen.getAllByTestId("citation-chip")).toHaveLength(2);
    expect(screen.getByTestId("unverified-number")).toHaveTextContent("99.9");
    expect(screen.getByText(/\[3\]/)).toBeInTheDocument(); // unknown marker stays text
  });

  it("does not underline a number inside a longer number or a marker", () => {
    expect(markAnswer("2018 and 18 and [18]", [], ["18"])).toBe("2018 and [18](#unverified) and [18]");
    expect(stripRawMarkers("ปิด 34.5 [^prov_01ABC] บาท")).toBe("ปิด 34.5 บาท");
  });
});
