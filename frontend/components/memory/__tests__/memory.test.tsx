import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MemoryView } from "@/components/memory/MemoryView";
import { ReasoningBank, tagList } from "@/components/memory/ReasoningBank";
import type { MemoryItemOut, ReasoningItemOut } from "@/lib/memory-api";

const replace = vi.fn();
let searchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  useSearchParams: () => searchParams,
  usePathname: () => "/memory",
}));

// -- a tiny in-memory backend behind fetch ---------------------------------------------------

interface Call {
  method: string;
  path: string;
  body: unknown;
  headers: Record<string, string>;
}

function item(over: Partial<MemoryItemOut> & Pick<MemoryItemOut, "id" | "text">): MemoryItemOut {
  return {
    category: "investment_style",
    key: null,
    value_json: null,
    confidence: 0.9,
    importance: 5,
    source: "user_stated",
    source_ref: {},
    status: "active",
    supersedes: null,
    superseded_by: null,
    valid_from: "2026-10-01T00:00:00Z",
    valid_to: null,
    sensitivity: "normal",
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    last_accessed_at: null,
    access_count: 0,
    ...over,
  };
}

function rbItem(over: Partial<ReasoningItemOut> & Pick<ReasoningItemOut, "id" | "title">): ReasoningItemOut {
  return {
    description: "คำอธิบายหนึ่งประโยค",
    content: "เนื้อหาบทเรียนแบบเต็ม",
    kind: "strategy",
    outcome_signal: "success",
    judge_score: 0.8,
    tags: { workflow: "portfolio", market: "SET" },
    use_count: 4,
    success_count: 3,
    success_rate: 0.67,
    source_count: 1,
    status: "active",
    prompt_version: "rb_distill.v1",
    model: "fake:m",
    source_run_id: "run_src_000001",
    created_at: "2026-10-10T00:00:00Z",
    updated_at: "2026-10-10T00:00:00Z",
    ...over,
  };
}

function backend() {
  const db: MemoryItemOut[] = [
    item({ id: "mem_old", text: "ผู้ใช้ชอบลงทุนเรื่อย ๆ ระยะยาว", status: "superseded", superseded_by: "mem_pace", valid_to: "2026-10-10T00:00:00Z" }),
    item({ id: "mem_pace", text: "ผู้ใช้ชอบลงทุนระยะสั้น เร็ว ๆ", supersedes: "mem_old", valid_from: "2026-10-10T00:00:00Z", importance: 8 }),
    item({ id: "mem_goal", text: "ผู้ใช้เก็บเงินดาวน์คอนโดปี 2028", category: "goal", importance: 9 }),
    item({ id: "mem_risk", text: "ผู้ใช้น่าจะรับความเสี่ยงสูง", category: "risk_tolerance", status: "candidate", source: "inferred_behavior", confidence: 0.6 }),
    item({ id: "mem_ret", text: "ผู้ใช้ไม่ชอบหุ้นธนาคาร", category: "preference", status: "retired", valid_to: "2026-10-12T00:00:00Z" }),
  ];
  const rb: ReasoningItemOut[] = [
    rbItem({ id: "rb_a", title: "ลดน้ำหนักหุ้นตัวเดียวก่อนเพิ่มตัวใหม่" }),
    rbItem({ id: "rb_b", title: "อย่าไล่ราคาหลังข่าวดี", kind: "pitfall", tags: { workflow: "trade", market: "US" }, success_rate: 0.3 }),
    rbItem({ id: "rb_c", title: "ถามเป้าหมายก่อน", kind: "coaching", tags: { workflow: "research" } }),
  ];
  const calls: Call[] = [];
  let n = 0;

  const counts = () => ({
    active: db.filter((m) => m.status === "active").length,
    candidate: db.filter((m) => m.status === "candidate").length,
    superseded: db.filter((m) => m.status === "superseded").length,
    retired: db.filter((m) => m.status === "retired").length,
  });
  const find = (id: string) => db.find((m) => m.id === id)!;
  const chainOf = (id: string): MemoryItemOut[] => {
    let first = find(id);
    while (first.supersedes) first = find(first.supersedes);
    const out = [first];
    while (out.at(-1)!.superseded_by) out.push(find(out.at(-1)!.superseded_by!));
    return out;
  };

  const route = (method: string, path: string, params: URLSearchParams, body: Record<string, unknown>): [number, unknown] => {
    if (method === "GET" && path === "/memory") return [200, { items: db, counts: counts() }];
    if (method === "GET" && path === "/memory/profile") {
      const active = db.filter((m) => m.status === "active");
      return [200, { profile_text: active.map((m) => `- ${m.text}`).join("\n"), token_estimate: 42, items_used: active.map((m) => m.id), updated_at: "2026-10-12T00:00:00Z" }];
    }
    if (method === "POST" && path === "/memory") {
      const created = item({ id: `mem_new${++n}`, text: String(body.text), category: body.category as MemoryItemOut["category"], source: "manual", confidence: 0.95 });
      const prev = find("mem_pace");
      if (body.category === "investment_style") {
        prev.status = "superseded";
        prev.superseded_by = created.id;
        created.supersedes = prev.id;
        db.push(created);
        return [200, { changes: [{ op: "SUPERSEDE", item: created, previous: prev }] }];
      }
      db.push(created);
      return [200, { changes: [{ op: "ADD", item: created, previous: null }] }];
    }
    const m = path.match(/^\/memory\/([^/]+)(?:\/(\w+))?$/);
    if (m) {
      const [, id, action] = m as unknown as [string, string, string | undefined];
      if (!db.some((x) => x.id === id)) return [404, { detail: "not found" }];
      const it = find(id);
      if (method === "POST" && action === "confirm") Object.assign(it, { status: "active", source: "user_confirmed", confidence: 0.95 });
      else if (method === "POST" && action === "reject") it.status = "rejected";
      else if (method === "POST" && action === "retire") Object.assign(it, { status: "retired", valid_to: "2026-10-17T00:00:00Z" });
      else if (method === "GET" && action === "history") return [200, { items: chainOf(id) }];
      else if (method === "PATCH" && !action) {
        const next = item({ ...it, id: `mem_edit${++n}`, text: String(body.text), supersedes: it.id, source: "manual" });
        Object.assign(it, { status: "superseded", superseded_by: next.id, valid_to: "2026-10-17T00:00:00Z" });
        db.push(next);
        return [200, next];
      } else if (method === "DELETE" && !action) {
        const gone = params.get("cascade") === "1" ? chainOf(id).map((x) => x.id) : [id];
        for (const g of gone) db.splice(db.findIndex((x) => x.id === g), 1);
        return [200, { deleted: gone }];
      }
      return [200, it];
    }
    if (method === "GET" && path === "/reasoning-bank/stats")
      return [200, { total: rb.length, active: rb.filter((r) => r.status === "active").length, by_kind: { strategy: 1, pitfall: 1, coaching: 1 }, by_workflow: {}, avg_success_rate: 0.55 }];
    if (method === "GET" && path === "/reasoning-bank") {
      const q = params.get("q") ?? "";
      const items = rb.filter(
        (r) =>
          (!params.get("workflow") || r.tags.workflow === params.get("workflow")) &&
          (!params.get("kind") || r.kind === params.get("kind")) &&
          (!q || r.title.includes(q)),
      );
      return [200, { items, counts: { active: items.length } }];
    }
    const r = path.match(/^\/reasoning-bank\/([^/]+)(?:\/(retire))?$/);
    if (r) {
      const it = rb.find((x) => x.id === r[1])!;
      if (method === "POST" && r[2]) {
        it.status = "retired";
        return [200, it];
      }
      return [200, { item: it, uses: [{ run_id: "run_use_000001", outcome: "success", created_at: "2026-10-15T00:00:00Z" }] }];
    }
    return [404, { detail: `no route ${method} ${path}` }];
  };

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input));
    const method = (init.method ?? "GET").toUpperCase();
    const path = url.pathname.replace(/^\/api/, "");
    const body = init.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
    calls.push({ method, path: path + url.search, body: init.body ? body : undefined, headers: (init.headers ?? {}) as Record<string, string> });
    const [status, data] = route(method, path, url.searchParams, body);
    return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { db, rb, calls };
}

beforeEach(() => {
  searchParams = new URLSearchParams();
  replace.mockClear();
});
afterEach(() => vi.unstubAllGlobals());

const waitList = () => screen.findAllByTestId("memory-item");
const rowOf = (text: string) => screen.getByText(text).closest("li") as HTMLElement;

describe("/memory · ความจำของฉัน", () => {
  it("shows the injected profile, candidates on top and active items grouped by category (Thai labels)", async () => {
    const { calls } = backend();
    render(<MemoryView />);
    await waitList();
    expect(await screen.findByTestId("memory-profile-text")).toHaveTextContent("ผู้ใช้ชอบลงทุนระยะสั้น เร็ว ๆ");
    expect(screen.getByText("~42 tokens")).toBeInTheDocument();
    expect(screen.getByText("AI เห็นสิ่งนี้ทุกครั้งที่คุยกับคุณ — แก้ได้โดยแก้รายการความจำด้านล่าง")).toBeInTheDocument();

    const candidates = screen.getByTestId("memory-candidates");
    expect(within(candidates).getByText("ผู้ใช้น่าจะรับความเสี่ยงสูง")).toBeInTheDocument();
    expect(within(candidates).getByText("AI อนุมาน")).toBeInTheDocument();

    const groups = screen.getAllByTestId("memory-group").map((g) => g.dataset.category);
    expect(groups).toEqual(["investment_style", "goal"]);
    expect(screen.getByText(/สไตล์การลงทุน · 1/)).toBeInTheDocument();
    expect(screen.getByText(/เป้าหมาย · 1/)).toBeInTheDocument();
    // superseded / retired hidden by default
    expect(screen.queryByText("ผู้ใช้ชอบลงทุนเรื่อย ๆ ระยะยาว")).not.toBeInTheDocument();
    expect(screen.getByTestId("memory-counts")).toHaveTextContent("ใช้อยู่ 2 · รอยืนยัน 1 · ถูกแทนที่ 1 · เลิกใช้ 1");
    expect(calls.find((c) => c.path.startsWith("/memory?"))?.path).toBe("/memory?status=all");
    expect(calls[0]!.headers["X-User-Id"]).toBe("usr_default");
  });

  it("toggle shows retired and superseded items", async () => {
    backend();
    render(<MemoryView />);
    await waitList();
    fireEvent.click(screen.getByTestId("show-inactive"));
    expect(screen.getByText("ผู้ใช้ชอบลงทุนเรื่อย ๆ ระยะยาว").closest("li")!.dataset.status).toBe("superseded");
    expect(screen.getByText("ผู้ใช้ไม่ชอบหุ้นธนาคาร").closest("li")!.dataset.status).toBe("retired");
  });

  it("confirms a candidate (POST …/confirm) and it moves into its category", async () => {
    const { calls } = backend();
    render(<MemoryView />);
    await waitList();
    fireEvent.click(within(rowOf("ผู้ใช้น่าจะรับความเสี่ยงสูง")).getByTestId("memory-confirm"));
    await waitFor(() => expect(screen.queryByTestId("memory-candidates")).not.toBeInTheDocument());
    expect(calls.some((c) => c.method === "POST" && c.path === "/memory/mem_risk/confirm")).toBe(true);
    expect(within(rowOf("ผู้ใช้น่าจะรับความเสี่ยงสูง")).getByText("ยืนยันแล้ว")).toBeInTheDocument();
    expect(screen.getByText(/การรับความเสี่ยง · 1/)).toBeInTheDocument();
  });

  it("edits inline → PATCH with the new text; the new row replaces the old one", async () => {
    const { calls } = backend();
    render(<MemoryView />);
    await waitList();
    const row = rowOf("ผู้ใช้เก็บเงินดาวน์คอนโดปี 2028");
    fireEvent.click(within(row).getByTestId("memory-edit"));
    fireEvent.change(within(row).getByLabelText("แก้ข้อความความจำ"), { target: { value: "ผู้ใช้เก็บเงินดาวน์คอนโดปี 2029" } });
    fireEvent.click(within(row).getByTestId("memory-edit-save"));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(await screen.findByText("ผู้ใช้เก็บเงินดาวน์คอนโดปี 2029")).toBeInTheDocument();
    expect(screen.queryByText("ผู้ใช้เก็บเงินดาวน์คอนโดปี 2028")).not.toBeInTheDocument();
    const patch = calls.find((c) => c.method === "PATCH")!;
    expect(patch.path).toBe("/memory/mem_goal");
    expect(patch.body).toEqual({ text: "ผู้ใช้เก็บเงินดาวน์คอนโดปี 2029" });
  });

  it("retires an active item (POST …/retire): gone from the active list, still visible with the toggle", async () => {
    const { calls } = backend();
    render(<MemoryView />);
    await waitList();
    fireEvent.click(within(rowOf("ผู้ใช้เก็บเงินดาวน์คอนโดปี 2028")).getByTestId("memory-retire"));
    await waitFor(() => expect(screen.queryByText("ผู้ใช้เก็บเงินดาวน์คอนโดปี 2028")).not.toBeInTheDocument());
    expect(calls.some((c) => c.method === "POST" && c.path === "/memory/mem_goal/retire")).toBe(true);
    fireEvent.click(screen.getByTestId("show-inactive"));
    expect(rowOf("ผู้ใช้เก็บเงินดาวน์คอนโดปี 2028").dataset.status).toBe("retired");
  });

  it("forget asks first, then DELETE ?cascade=1 removes the whole chain", async () => {
    const { calls, db } = backend();
    render(<MemoryView />);
    await waitList();
    fireEvent.click(within(rowOf("ผู้ใช้ชอบลงทุนระยะสั้น เร็ว ๆ")).getByTestId("memory-forget"));
    const dialog = screen.getByRole("dialog", { name: "ลืมความจำ" });
    expect(within(dialog).getByText("ลบถาวรรวมประวัติ?")).toBeInTheDocument();
    // cancel: nothing deleted
    fireEvent.click(within(dialog).getByText("ยกเลิก"));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);

    fireEvent.click(within(rowOf("ผู้ใช้ชอบลงทุนระยะสั้น เร็ว ๆ")).getByTestId("memory-forget"));
    fireEvent.click(screen.getByTestId("forget-confirm"));
    await waitFor(() => expect(screen.queryByText("ผู้ใช้ชอบลงทุนระยะสั้น เร็ว ๆ")).not.toBeInTheDocument());
    expect(calls.find((c) => c.method === "DELETE")!.path).toBe("/memory/mem_pace?cascade=1");
    expect(db.some((m) => m.id === "mem_old" || m.id === "mem_pace")).toBe(false);
    fireEvent.click(screen.getByTestId("show-inactive"));
    expect(screen.queryByText("ผู้ใช้ชอบลงทุนเรื่อย ๆ ระยะยาว")).not.toBeInTheDocument();
  });

  it("history drawer shows the supersede chain old → new with valid_from / valid_to", async () => {
    backend();
    render(<MemoryView />);
    await waitList();
    fireEvent.click(within(rowOf("ผู้ใช้ชอบลงทุนระยะสั้น เร็ว ๆ")).getByTestId("memory-history"));
    const drawer = screen.getByRole("dialog", { name: "ประวัติความจำ" });
    const entries = await within(drawer).findAllByTestId("history-item");
    expect(entries.map((e) => e.querySelector("p")!.textContent)).toEqual(["ผู้ใช้ชอบลงทุนเรื่อย ๆ ระยะยาว", "ผู้ใช้ชอบลงทุนระยะสั้น เร็ว ๆ"]);
    expect(within(entries[0]!).getByText("ถูกแทนที่")).toBeInTheDocument();
    const validity = within(drawer).getAllByTestId("history-validity").map((v) => v.textContent);
    expect(validity[0]).toMatch(/→ (?!ปัจจุบัน)/);
    expect(validity[1]).toMatch(/→ ปัจจุบัน$/);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "ประวัติความจำ" })).not.toBeInTheDocument();
  });

  it("add form posts category + text and shows the resulting changes", async () => {
    const { calls } = backend();
    render(<MemoryView />);
    await waitList();
    const form = screen.getByTestId("add-memory");
    fireEvent.change(within(form).getByLabelText("หมวด"), { target: { value: "investment_style" } });
    fireEvent.change(within(form).getByLabelText("ข้อความความจำ"), { target: { value: "ผู้ใช้กลับมาชอบลงทุนระยะยาว" } });
    fireEvent.click(within(form).getByTestId("add-memory-submit"));
    expect(await within(form).findByTestId("add-memory-change")).toHaveTextContent(
      "แทนที่: ผู้ใช้ชอบลงทุนระยะสั้น เร็ว ๆ → ผู้ใช้กลับมาชอบลงทุนระยะยาว",
    );
    const post = calls.find((c) => c.method === "POST" && c.path === "/memory")!;
    expect(post.body).toEqual({ category: "investment_style", text: "ผู้ใช้กลับมาชอบลงทุนระยะยาว" });
    // list + profile reloaded
    await waitFor(() => expect(screen.getAllByText("ผู้ใช้กลับมาชอบลงทุนระยะยาว").length).toBeGreaterThan(0));
    expect(screen.getByTestId("memory-profile-text")).toHaveTextContent("ผู้ใช้กลับมาชอบลงทุนระยะยาว");
  });

  it("switching to the ReasoningBank tab replaces the URL", async () => {
    backend();
    render(<MemoryView />);
    await waitList();
    fireEvent.click(screen.getByTestId("memory-tab-reasoning"));
    expect(replace).toHaveBeenCalledWith("/memory?tab=reasoning");
  });
});

describe("/memory · ReasoningBank", () => {
  it("renders stats, rows with kind badges, workflow, tags, uses and success-rate bars", async () => {
    backend();
    searchParams = new URLSearchParams("tab=reasoning");
    render(<MemoryView />);
    const rows = await screen.findAllByTestId("rb-row");
    expect(rows).toHaveLength(3);
    expect(screen.getByTestId("rb-stats")).toHaveTextContent("55%");
    expect(rows.map((r) => within(r).getByTestId("kind-badge").textContent)).toEqual(["strategy", "pitfall", "coaching"]);
    expect(within(rows[1]!).getByText("trade")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("market:US")).toBeInTheDocument();
    expect(within(rows[1]!).getByRole("meter", { name: "success rate" })).toHaveAttribute("aria-valuenow", "30");
  });

  it("filters by workflow, kind and a debounced query", async () => {
    const { calls } = backend();
    render(<ReasoningBank />);
    await screen.findAllByTestId("rb-row");
    fireEvent.change(screen.getByLabelText("workflow"), { target: { value: "trade" } });
    await waitFor(() => expect(screen.getAllByTestId("rb-row")).toHaveLength(1));
    fireEvent.change(screen.getByLabelText("workflow"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("kind"), { target: { value: "coaching" } });
    await waitFor(() => expect(screen.getAllByTestId("rb-row")).toHaveLength(1));
    expect(screen.getByText("ถามเป้าหมายก่อน")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("kind"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("ค้นหาบทเรียน"), { target: { value: "ไล่ราคา" } });
    await waitFor(() => expect(calls.some((c) => c.path.includes("q=%E0"))).toBe(true));
    await waitFor(() => expect(screen.getAllByTestId("rb-row")).toHaveLength(1));
    expect(screen.getByText("อย่าไล่ราคาหลังข่าวดี")).toBeInTheDocument();
  });

  it("detail drawer shows content + uses and retires the item", async () => {
    const { calls } = backend();
    render(<ReasoningBank />);
    const rows = await screen.findAllByTestId("rb-row");
    fireEvent.click(rows[0]!);
    const detail = await screen.findByTestId("rb-detail");
    expect(within(detail).getByText("เนื้อหาบทเรียนแบบเต็ม")).toBeInTheDocument();
    expect(within(detail).getAllByTestId("rb-use")).toHaveLength(1);
    fireEvent.click(within(detail).getByTestId("rb-retire"));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/reasoning-bank/rb_a/retire")).toBe(true));
    await waitFor(() => expect(screen.queryByTestId("rb-retire")).not.toBeInTheDocument());
  });

  it("tagList leaves out workflow and empty tags", () => {
    expect(tagList({ workflow: "trade", market: "US", regime: null, horizon: "" })).toEqual(["market:US"]);
  });
});
