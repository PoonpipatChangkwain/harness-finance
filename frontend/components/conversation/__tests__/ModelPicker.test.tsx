import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { KeyWarning, ModelPicker, choiceProblem } from "@/components/conversation/ModelPicker";
import type { LlmProviders } from "@/lib/api";

const providers: LlmProviders = {
  default: {
    deep: { provider: "groq", model: "openai/gpt-oss-120b", configured: true },
    quick: { provider: "groq", model: "openai/gpt-oss-120b", configured: true },
  },
  providers: [
    {
      id: "groq",
      label: "Groq",
      configured: true,
      env_hint: "GROQ_API_KEY",
      free: false,
      recommended: { quick: "openai/gpt-oss-120b", deep: "openai/gpt-oss-120b" },
      models: ["openai/gpt-oss-120b", "openai/gpt-oss-20b"],
    },
    {
      id: "anthropic",
      label: "Anthropic (Claude)",
      configured: false,
      env_hint: "ANTHROPIC_API_KEY",
      free: false,
      recommended: { quick: "claude-haiku-4-5", deep: "claude-sonnet-5" },
      models: ["claude-sonnet-5", "claude-haiku-4-5"],
    },
  ],
};

describe("ModelPicker", () => {
  it("offers Default plus every provider marked by key status", () => {
    render(<ModelPicker providers={providers} choice={null} onChange={() => {}} />);
    const select = screen.getByTestId("provider-picker");
    expect(select).toHaveTextContent("Default · groq:openai/gpt-oss-120b");
    expect(select).toHaveTextContent("✓ Groq");
    expect(select).toHaveTextContent("✗ Anthropic (Claude) (ไม่มี key)");
    expect(screen.queryByTestId("model-input")).not.toBeInTheDocument();
  });

  it("picking a provider pre-fills its recommended model; Default clears the choice", () => {
    const onChange = vi.fn();
    render(<ModelPicker providers={providers} choice={null} onChange={onChange} />);
    fireEvent.change(screen.getByTestId("provider-picker"), { target: { value: "anthropic" } });
    expect(onChange).toHaveBeenLastCalledWith({ provider: "anthropic", model: "claude-haiku-4-5" });
    fireEvent.change(screen.getByTestId("provider-picker"), { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it("lists every suggested model, whatever is currently picked", () => {
    const onChange = vi.fn();
    render(<ModelPicker providers={providers} choice={{ provider: "groq", model: "openai/gpt-oss-120b" }} onChange={onChange} />);
    const select = screen.getByTestId("model-select");
    expect(select).toHaveTextContent("openai/gpt-oss-120b");
    expect(select).toHaveTextContent("openai/gpt-oss-20b");
    expect(screen.queryByTestId("model-input")).not.toBeInTheDocument();
    fireEvent.change(select, { target: { value: "openai/gpt-oss-20b" } });
    expect(onChange).toHaveBeenLastCalledWith({ provider: "groq", model: "openai/gpt-oss-20b" });
  });

  it("'other…' clears the model and a non-suggested id gets a text box", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <ModelPicker providers={providers} choice={{ provider: "groq", model: "openai/gpt-oss-120b" }} onChange={onChange} />,
    );
    fireEvent.change(screen.getByTestId("model-select"), { target: { value: "__custom__" } });
    expect(onChange).toHaveBeenLastCalledWith({ provider: "groq", model: "" });
    rerender(<ModelPicker providers={providers} choice={{ provider: "groq", model: "" }} onChange={onChange} />);
    fireEvent.change(screen.getByTestId("model-input"), { target: { value: "llama-3.3-70b-versatile" } });
    expect(onChange).toHaveBeenLastCalledWith({ provider: "groq", model: "llama-3.3-70b-versatile" });
  });
});

describe("choiceProblem", () => {
  it("names the env var when the key is missing", () => {
    const msg = choiceProblem({ provider: "anthropic", model: "claude-sonnet-5" }, providers.providers);
    expect(msg).toContain("ANTHROPIC_API_KEY");
    render(<KeyWarning message={msg!} />);
    expect(screen.getByRole("alert")).toHaveTextContent("ยังไม่ได้ใส่ API key ของ Anthropic (Claude)");
  });

  it("is fine for the default, a configured provider, and needs a model name", () => {
    expect(choiceProblem(null, providers.providers)).toBeNull();
    expect(choiceProblem({ provider: "groq", model: "openai/gpt-oss-20b" }, providers.providers)).toBeNull();
    expect(choiceProblem({ provider: "groq", model: " " }, providers.providers)).toBe("ใส่ชื่อ model ก่อน");
  });
});
