import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { move } from "@/components/settings/OrderList";
import { SettingsView, changedNumbers, changedOrders, policyBody } from "@/components/settings/SettingsView";
import type { SettingsUpdate, SettingsView as View } from "@/lib/settings-api";

function fakeView(): View {
  const values = {
    llm: {
      deep: { provider: "groq", model: "llama-deep", configured: true },
      quick: { provider: "groq", model: "llama-quick", configured: true },
      failover: [{ provider: "openai", model: "gpt-x", configured: false }],
    },
    data: { order: { quote: ["yfinance", "finnhub", "alpha_vantage"], macro: ["fred"] } },
    limits: {
      max_iterations: 10, max_tool_calls_total: 40, max_calls_per_tool: 3, similarity_threshold: 0.7, tool_timeout_s: 30,
      run_wall_clock_s: 600, max_cost_usd: 1, mc_count_trigger: null, mc_keep_recent: null, mc_token_trigger: null,
      autocompact_buffer_tokens: null,
    },
    policy: {
      max_position_weight: null, max_sector_weight: null, max_risk_per_trade: null, min_confidence: null,
      block_on_low_confidence: true, min_risk_reward: null, require_stop_loss: true, max_turnover: null,
    },
  };
  return {
    values,
    defaults: structuredClone(values),
    policy_builtin: {
      max_position_weight: 0.2, max_sector_weight: null, max_risk_per_trade: 0.02, min_confidence: 0.7,
      block_on_low_confidence: true, min_risk_reward: 1, require_stop_loss: true, max_turnover: null,
    },
    overrides: { llm: {}, data: {}, limits: {}, policy: {} },
    catalog: {
      llm: [
        { id: "groq", label: "Groq", configured: true, env_hint: "GROQ_API_KEY", free: true, recommended: { quick: "q", deep: "d" }, models: ["llama-deep"] },
        { id: "openai", label: "OpenAI", configured: false, env_hint: "OPENAI_API_KEY", free: false, recommended: { quick: null, deep: null }, models: [] },
      ],
      data: {
        quote: {
          mode: "failover",
          providers: [
            { id: "yfinance", registered: true, configured: true, tier: "A" },
            { id: "finnhub", registered: true, configured: false, tier: "B" },
            { id: "alpha_vantage", registered: true, configured: false, tier: "B" },
          ],
        },
        macro: { mode: "failover", providers: [{ id: "fred", registered: true, configured: false, tier: "B" }] },
      },
    },
  } as View;
}

function mockApi() {
  const view = fakeView();
  const put = vi.fn(async (body: SettingsUpdate) => {
    const next = structuredClone(view);
    if (body.data?.order) Object.assign(next.values.data.order, body.data.order);
    return next;
  });
  return { get: vi.fn(async () => view), put };
}

describe("Settings", () => {
  it("drag reorder of a capability → PUT body with only the changed order", async () => {
    const api = mockApi();
    render(<SettingsView api={api} />);
    const quote = await screen.findByTestId("cap-quote");
    const items = within(quote).getAllByTestId("order-item");
    expect(items.map((i) => i.dataset.key)).toEqual(["yfinance", "finnhub", "alpha_vantage"]);
    const data = { setData: () => {}, effectAllowed: "", dropEffect: "" };
    fireEvent.dragStart(items[2]!, { dataTransfer: data });
    fireEvent.dragOver(items[0]!, { dataTransfer: data });
    fireEvent.drop(items[0]!, { dataTransfer: data });
    expect(within(quote).getAllByTestId("order-item").map((i) => i.dataset.key)).toEqual(["alpha_vantage", "yfinance", "finnhub"]);
    fireEvent.click(screen.getByTestId("save-data"));
    await waitFor(() => expect(api.put).toHaveBeenCalledTimes(1));
    expect(api.put).toHaveBeenCalledWith({ data: { order: { quote: ["alpha_vantage", "yfinance", "finnhub"] } } });
  });

  it("↑/↓ buttons reorder too, and Reset sends an empty section", async () => {
    const api = mockApi();
    render(<SettingsView api={api} />);
    const quote = await screen.findByTestId("cap-quote");
    fireEvent.click(within(quote).getByLabelText("เลื่อน finnhub ขึ้น"));
    expect(within(quote).getAllByTestId("order-item")[0]!.dataset.key).toBe("finnhub");
    fireEvent.click(screen.getByTestId("reset-data"));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith({ data: {} }));
  });

  it("shows key status and env hints but never sends or reads a key", async () => {
    const api = mockApi();
    render(<SettingsView api={api} />);
    const llm = await screen.findByTestId("section-llm");
    expect(llm.textContent).toContain("OPENAI_API_KEY");
    fireEvent.change(within(llm).getByLabelText("deep model"), { target: { value: "llama-big" } });
    fireEvent.click(screen.getByTestId("save-llm"));
    await waitFor(() => expect(api.put).toHaveBeenCalled());
    const body = api.put.mock.calls[0]![0];
    expect(body).toEqual({
      llm: {
        deep: { provider: "groq", model: "llama-big" },
        quick: { provider: "groq", model: "llama-quick" },
        failover: [{ provider: "openai", model: "gpt-x" }],
      },
    });
    expect(JSON.stringify(body)).not.toMatch(/api_key|secret|token/i);
    expect(document.body.innerHTML).not.toMatch(/sk-|gsk_/);
  });

  it("limits / policy bodies carry only what changed, in fractions", async () => {
    expect(changedNumbers({ max_iterations: "6", max_tool_calls_total: "40", tool_timeout_s: "" }, { max_iterations: 10, max_tool_calls_total: 40, tool_timeout_s: 30 })).toEqual({
      max_iterations: 6,
    });
    const empty = { max_position_weight: "", max_sector_weight: "", max_risk_per_trade: "", min_confidence: "", max_turnover: "", min_risk_reward: "" };
    const on = { require_stop_loss: true, block_on_low_confidence: true };
    expect(
      policyBody({ ...empty, max_position_weight: "15", min_confidence: "70", min_risk_reward: "1.5" }, { require_stop_loss: false, block_on_low_confidence: false }),
    ).toEqual({
      max_position_weight: 0.15,
      min_confidence: 0.7,
      min_risk_reward: 1.5,
      require_stop_loss: false,
      block_on_low_confidence: false,
    });
    expect(policyBody(empty, on)).toEqual({});
    expect(policyBody({ ...empty, min_risk_reward: "abc" }, on)).toEqual({});
    expect(changedOrders({ a: ["x", "y"], b: ["z"] }, { a: ["y", "x"], b: ["z"] })).toEqual({ a: ["x", "y"] });
    expect(move(["a", "b", "c"], 0, 2)).toEqual(["b", "c", "a"]);

    const api = mockApi();
    render(<SettingsView api={api} />);
    const policy = await screen.findByTestId("section-policy");
    fireEvent.change(within(policy).getByLabelText("max_position_weight"), { target: { value: "15" } });
    fireEvent.click(screen.getByTestId("save-policy"));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith({ policy: { max_position_weight: 0.15 } }));
  });

  it("policy: min R:R is a plain ratio and the low-confidence block can be turned off", async () => {
    const api = mockApi();
    render(<SettingsView api={api} />);
    const policy = await screen.findByTestId("section-policy");
    expect(within(policy).getByLabelText<HTMLInputElement>("block_on_low_confidence").checked).toBe(true);
    fireEvent.change(within(policy).getByLabelText("min_risk_reward"), { target: { value: "2" } });
    fireEvent.click(within(policy).getByLabelText("block_on_low_confidence"));
    fireEvent.click(screen.getByTestId("save-policy"));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith({ policy: { min_risk_reward: 2, block_on_low_confidence: false } }));
  });
});
