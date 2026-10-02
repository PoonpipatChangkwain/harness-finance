import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Sidebar } from "@/components/shell/Sidebar";
import type { SessionListItem } from "@/lib/api";
import { useSessionStore } from "@/stores/session-store";

const push = vi.fn();
let pathname = "/research/ses_A";
vi.mock("next/navigation", () => ({
  usePathname: () => pathname,
  useRouter: () => ({ push, replace: vi.fn() }),
}));
vi.mock("@/components/shell/ProviderStatus", () => ({ ProviderStatus: () => null }));

const session = (id: string, title: string): SessionListItem => ({
  id,
  kind: "research",
  title,
  created_at: "2026-09-26T00:00:00Z",
  updated_at: "2026-09-26T00:00:00Z",
  last_run: null,
});

describe("Sidebar session delete", () => {
  beforeEach(() => {
    push.mockReset();
    pathname = "/research/ses_A";
    useSessionStore.setState({
      sessions: [session("ses_A", "Apple stock"), session("ses_B", "PTT")],
      loaded: true,
      error: null,
      reload: async () => {},
    });
  });

  it("asks first, deletes, and leaves the page of the deleted session", async () => {
    const remove = vi.fn(async (id: string) => {
      useSessionStore.setState((s) => ({ sessions: s.sessions.filter((x) => x.id !== id) }));
      return null;
    });
    useSessionStore.setState({ remove });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<Sidebar compact={false} />);
    fireEvent.click(screen.getByRole("button", { name: "ลบ session Apple stock" }));

    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("Apple stock"));
    await waitFor(() => expect(remove).toHaveBeenCalledWith("ses_A"));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/research"));
    expect(screen.queryByText("Apple stock")).not.toBeInTheDocument();
    expect(screen.getByText("PTT")).toBeInTheDocument();
  });

  it("does nothing when the confirm is cancelled and shows a refusal", async () => {
    const remove = vi.fn(async () => "session นี้มี run กำลังทำงานอยู่ กด Stop ก่อนแล้วค่อยลบ");
    useSessionStore.setState({ remove });
    vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);

    render(<Sidebar compact={false} />);
    fireEvent.click(screen.getByRole("button", { name: "ลบ session PTT" }));
    expect(remove).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "ลบ session PTT" }));
    expect(await screen.findByText(/กด Stop ก่อน/)).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it("has no delete button in the icon-only sidebar", () => {
    render(<Sidebar compact />);
    expect(screen.queryByTestId("delete-session")).not.toBeInTheDocument();
  });
});
