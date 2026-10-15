# UI Spec — หน้าตาและพฤติกรรมของ frontend

**หลักการ:** ยึดแบบ deepseek-harness web client คือ **"เห็น harness ทำงานทุกขั้น"** ผู้ใช้ต้องเห็นว่า agent เรียก tool อะไร ด้วย args อะไร ได้ผลอะไร ใช้เวลาเท่าไร ใช้ context ไปเท่าไร เกิด compaction เมื่อไหร่ ตัวเลขแต่ละตัวมาจากไหน และตอนนี้ graph อยู่ที่ node ไหน

Reference หลัก (อ่านเพื่อดูพฤติกรรมและ information design เท่านั้น ห้ามลอก Cordis/plugin framework):

| สิ่งที่ดู | ไฟล์ใน `../deepseek-harness/packages/client/` |
|----------|-----------------------------------------------|
| Layout 3 คอลัมน์ + drag handle | `ui-layout/src/client/AppFrame.tsx`, `columns.ts` |
| Transcript, turn, process | `ui-chat/src/client/chat/ChatView.tsx`, `TurnProcessNodeView.tsx`, `TurnTriggerNodeView.tsx`, `TurnTailNodeView.tsx`, `step-process.ts` |
| Tool call tree / row | `ui-tool/src/client/tool/ToolCallTree.tsx`, `components/ToolRow.tsx`, `components/ToolDetails.tsx`, `components/PreparingToolRow.tsx` |
| Reasoning | `ui-chat/src/client/chat/ReasoningRow.tsx` |
| Compaction marker | `ui-chat/src/client/chat/CompactionItem.tsx`, `CompactionCommandCard.tsx` |
| Context meter | `ui-conversation/src/client/context-occupancy.ts` |
| Stats / usage | `ui-chat/src/client/chat/StatsPills.tsx`, `TurnUsagePanel.tsx`, `token-format.ts` |
| Turn navigator | `ui-chat/src/client/chat/TurnNavigator.tsx` |
| Approval takeover | `ui-approval/src/client/ApprovalPanel.tsx` (Enter = อนุมัติ, Esc = ปฏิเสธ) |
| Workflow run | `ui-workflow-run/src/client/WorkflowRunPanel.tsx` (status running/completed/failed/cancelled/interrupted) |
| Trajectory ledger + timeline | `ui-trajectory/src/client/TrajectoryView.tsx`, `TrajectoryTable.tsx`, `TrajectoryTimeline.tsx` |
| Right sidebar tabs | `ui-sidebar-right/src/client/shell/SidebarRight.tsx`, `tab-registry.ts` |
| Theme tokens | `ui-theme/src/styles/base.css` (`--dsw-*`) |

## 1. Frontend stack

| เรื่อง | เลือก | เหตุผล |
|--------|-------|--------|
| Framework | Next.js 15 (App Router) + React 19 + TypeScript strict | ตามที่ตกลงไว้ ทุกหน้าเป็น client component เพราะเป็น app แบบ interactive ใช้ server component แค่ใน layout shell |
| Package manager | pnpm | |
| Styling | Tailwind CSS v4 + CSS variables (design tokens `--fh-*`) | token แบบเดียวกับ `--dsw-*` จึงทำ light/dark ได้ |
| Components | shadcn/ui (Radix) + lucide-react | ใช้ primitive ที่ accessible ทันที |
| State | Zustand (store ต่อ run + UI store) | FinRobot Desktop ใช้ตัวเดียวกัน และเบากว่า Redux |
| SSE | `@microsoft/fetch-event-source` | รองรับ `Last-Event-ID`, retry และ header |
| Markdown | `react-markdown` + `remark-gfm` | answer / report |
| กราฟราคา | `lightweight-charts` (TradingView) | candlestick + indicator overlay |
| กราฟทั่วไป | Recharts | allocation donut, bar ของ metric |
| Graph view | `@xyflow/react` (React Flow) | แสดง pipeline ของ trading แบบ live |
| ตาราง | `@tanstack/react-table` | holdings และ trajectory |
| Form | `react-hook-form` + `zod` | ฟอร์มพอร์ต และ trade input |
| JSON viewer | `react-json-view-lite` | args/output ของ tool |
| ฟอนต์ | `IBM Plex Sans Thai` (UI) + `JetBrains Mono` (ตัวเลข/โค้ด) ผ่าน `next/font` | รองรับภาษาไทย และตัวเลขใช้ `tabular-nums` |
| Test | Vitest + Testing Library; Playwright (Phase 13) | |

## 2. Design tokens

`frontend/app/globals.css` ประกาศใน `:root` (light) และ `[data-theme="dark"]` โดยให้ default เป็น dark และสลับได้ 3 โหมด: system, light, dark

```
--fh-bg, --fh-bg-layer-1, --fh-bg-layer-2, --fh-border, --fh-border-strong
--fh-text, --fh-text-muted, --fh-text-subtle
--fh-accent (ฟ้า), --fh-accent-muted
--fh-state-ongoing (ฟ้า + pulse), --fh-state-done (เขียว), --fh-state-error (แดง), --fh-state-warning (อำพัน), --fh-state-idle (เทา)
--fh-up (เขียว), --fh-down (แดง)   # สีราคา
--fh-verdict-approve, --fh-verdict-review, --fh-verdict-reject
--fh-radius-sm 6px, --fh-radius-md 10px, --fh-radius-lg 14px
--fh-font-ui, --fh-font-mono
```

component `StateDot` (ported concept จาก dsh primitives) มีสถานะ `ongoing | done | error | warning | idle` และทุกที่ที่แสดงสถานะต้องใช้ตัวนี้

## 3. App shell (ทุกหน้า)

```
┌──────────┬──────────────────────────────────────┬─────────────────────┐
│ Sidebar  │ Main                                 │ Inspector (right)   │
│ 240px    │ flex                                 │ 380px, ลากปรับได้     │
│ ─ Logo   │                                      │ 320–640px, ปิดได้      │
│ ─ Nav:   │                                      │ tabs ตามหน้า         │
│  Research│                                      │                     │
│  Portfolio                                      │                     │
│  Trade   │                                      │                     │
│ ─ Sessions/Runs list (ของหน้านั้น)               │                     │
│ ─ Settings, theme, provider status               │                     │
└──────────┴──────────────────────────────────────┴─────────────────────┘
```

- มี drag handle ระหว่างคอลัมน์ (อิง `AppFrame.tsx` DragHandle) และจำความกว้างใน `localStorage`
- sidebar ยุบเหลือ 56px (icon) เมื่อจอ < 1280px ส่วน inspector กลายเป็น drawer เมื่อจอ < 1024px และ sidebar เป็น sheet เมื่อจอ < 768px
- ท้าย sidebar มี **provider status** เป็นจุดสีต่อ provider ที่ดึงจาก `/config/status` (เขียว = configured, เทา = ไม่มี key)
- คีย์ลัด: `Ctrl+K` เปิด command palette (สลับหน้า, เปิด session, ค้น symbol), `Ctrl+.` เปิด/ปิด inspector, `Esc` หยุด run

## 4. หน้า Research Chat (`/research`, `/research/[sessionId]`)

### 4.1 Main column

```
Header:  [ชื่อ session]  · model chip (deep/quick) · ContextMeter ▮▮▮▯ 42% · $0.031 · ⏱ 38s
─────────────────────────────────────────────────────────────
Turn 1
  👤 User bubble: "วิเคราะห์ PTT หลังงบ Q2 ..."   [scenario chip: Earnings Review]
  ▸ Process · 9 steps · 6 tools · 31.2s                     ← TurnProcess (ยุบได้, default เปิดระหว่าง run, ยุบเมื่อเสร็จ)
     💭 Reasoning (ยุบได้, แสดง 2 บรรทัดแรก)
     ⚙ get_prices  PTT.BK · 1y · 250 bars            ● 0.8s   ← ToolRow
     ⚙ get_statements PTT.BK · quarterly             ● 1.2s
     ⚙ search_news  "PTT Q2 2026"  · 12 items        ● 2.1s
       └ ⚙ web_fetch  bangkokpost.com/...            ● 0.9s   ← subcall (parent_call_id)
     ⟲ Context compacted · 14 items · 38.2k → 6.1k tokens     ← CompactionItem (กดดู summary)
     ⚠ reddit_rss unavailable (429)                            ← warning row
     ⛔ get_prices blocked: similar query (3/3)                 ← guard.limit row
  🤖 Answer (markdown) พร้อม citation chips [1] [2] บนตัวเลข
     ⚠ 2 ตัวเลขไม่พบใน provenance (hover เพื่อดู)             ← unverified_numbers
  StatsPills: in 52.1k · out 3.4k · $0.031 · 31.2s · 3 LLM calls
─────────────────────────────────────────────────────────────
Composer: [Scenario ▾] [textarea …]                 [Send] / [■ Stop]
```

**ToolRow** (อิง `ToolRow.tsx`):
- แถวที่ยุบอยู่: icon ตาม category (data 📈, compute 🧮, search 🔎, memory 🧠, skill 🧩), ชื่อ tool, `summary` ที่ตัดด้วย ellipsis, StateDot และ duration ทางขวา
- ขณะรัน: ข้อความ `tool.progress` แสดงแบบ shimmer (อิง `TextShimmer`)
- เมื่อ error: summary ถูกแทนด้วยบรรทัดแรกของ error และ dot เป็นสีแดง
- เมื่อกางออก: แสดง **Arguments** (JSON viewer), **Output** (preview ≤ 2k chars, มีปุ่ม "เปิดเต็มใน Scratchpad"), **Provenance** (chip ของแต่ละ id) และ meta (read_only, batch_id, retry count)
- tool ที่รันพร้อมกันใน batch เดียวกันมีเส้นแนวตั้งเชื่อมทางซ้ายเพื่อบอกว่ารัน concurrent

**CompactionItem** (อิง `CompactionItem.tsx`): แสดง "Context compacted · N items · X → Y tokens" กดแล้วกาง summary ออกเป็น markdown ถ้าเป็น microcompact ให้แสดงบรรทัดจางๆ ว่า "Cleared N old tool results (−Z tokens)"

**ContextMeter** (อิง `context-occupancy.ts`): แถบ + % โดย percent = min(100, round(used / window × 100)) สีเปลี่ยนตามระดับ (< 60% ปกติ, 60–85% อำพัน, > 85% แดง) และ hover แล้วเห็น used/window กับ threshold ของการ compact

**Answer**: markdown ที่ตัวเลขซึ่งมี citation เป็น chip `[n]` เมื่อ hover จะเห็น popover ของ provenance (value, source, as_of, calculation) และเมื่อคลิกจะเปิด tab Provenance ใน inspector ส่วนตัวเลขใน `unverified_numbers` ให้ขีดเส้นใต้หยักสีอำพัน

**Composer**:
- Scenario picker ดึงจาก `/skills` แสดงชื่อ, คำอธิบาย และ category (research/analysis/data_processing/reporting) โดย "Auto" คือให้ agent เลือกเอง
- ระหว่าง run ปุ่ม Send กลายเป็น Stop (`POST /runs/{id}/cancel`)
- เมื่อมี `approval.required` ให้ **ApprovalPanel เข้ามาแทน composer** (takeover แบบ dsh) แสดง proposal, ผล policy (✓/✗ ทีละข้อ) และปุ่ม Approve (Enter) / Reject (Esc) / Edit

### 4.2 Inspector tabs (Research)

| Tab | เนื้อหา | แหล่งข้อมูล |
|-----|---------|------------|
| **Scratchpad** | รายการ entry ของ run (`init`, `tool_result`, `thinking`, `compaction`, `final`) พร้อม timestamp กดแล้วกาง JSON เต็มได้ มีช่อง filter ตาม tool และปุ่ม download JSONL | `GET /runs/{id}/scratchpad` + live จาก events |
| **Provenance** | ตาราง record ของ run ทั้งหมด (value, unit, source, tool, as_of, agent) คลิก record แล้วเห็น **lineage tree** (inputs → calculation → output) | `GET /runs/{id}/provenance`, `/provenance/{pid}?lineage=1` |
| **Context** | used/window, threshold ของ micro และ full compact, จำนวน tool result ที่ active/cleared และประวัติการ compact (ก่อน → หลัง) | `context.*` events |
| **Trajectory** | ledger ของทุก event (seq, time, type, node, tool, duration) + timeline bar แนวนอน (อิง `TrajectoryTimeline.tsx`) ที่เห็นช่วงเวลาซ้อนกันของ tool ที่รัน concurrent | events ทั้งหมด |

## 5. หน้า Trade (`/trade`, `/trade/[runId]`)

```
┌ Main ─────────────────────────────────────────────────────────────┐
│ Setup bar: [Symbol search: PTT ▾ (stock·TH)] [Date: 2026-09-24]    │
│   Analysts: ☑Market ☑News ☑Fundamentals ☑Sentiment ☐Macro         │
│   Debate rounds [1] Risk rounds [1]  ☑ ใช้พอร์ตของฉัน   [▶ Run]      │
├───────────────────────────────────────────────────────────────────┤
│ Price chart (lightweight-charts): candles + SMA20/50 + volume      │
│   toggle: RSI, MACD panes · range 1M/3M/6M/1Y/5Y                   │
├───────────────────────────────────────────────────────────────────┤
│ Pipeline graph (React Flow, auto-layout ซ้าย→ขวา)                    │
│  [Market]─┐                                                        │
│  [News]───┤→[Bull ⇄ Bear]→[Research Mgr]→[Trader]→[Risk: Agg⇄Con⇄Neu]│
│  [Fund.]──┤                                     →[Portfolio Mgr]→[Policy Gate]→[Approval]→[Report]
│  [Sent.]──┘                                                        │
│  node: StateDot + label + duration + badge(tool count)            │
│  node ที่มี checkpoint: ไอคอน 💾 · node ที่ interrupt: ขอบอำพันกะพริบ      │
├───────────────────────────────────────────────────────────────────┤
│ Decision card (เมื่อ PM เสร็จ):                                       │
│  Rating: Overweight · Verdict: REVIEW (0.72) · Entry 34.50 · SL 32.8│
│  · TP 38.0 · Size 5% (computed) · reason…  · policy ✓✓✗             │
└───────────────────────────────────────────────────────────────────┘
```

- คลิก node → inspector tab **Node** แสดง transcript ของ node นั้นด้วย ToolCallTree และ ReasoningRow ชุดเดียวกับ Research รวมถึง report markdown ที่ node เขียน และ structured output (JSON + rendered)
- **Debate view** (tab ใน inspector เมื่อคลิก Bull⇄Bear หรือ Risk): แสดงข้อความสลับเป็นคอลัมน์ Bull | Bear (หรือ Agg | Con | Neu) เรียงตามรอบ
- ถ้า run ล้มกลางทาง node นั้นเป็นสีแดงและมีปุ่ม **Resume from checkpoint** (`POST /runs/{id}/resume`) พร้อมบอกว่าจะเริ่มที่ node ไหน
- Sidebar แสดงประวัติ run เป็น `symbol · date · rating · verdict · status`
- Forex: ซ่อน checkbox Fundamentals, เปิด Macro เป็น default และให้ chart แสดง pip

### Inspector tabs (Trade)
`Node` · `Debate` · `Scratchpad` · `Provenance` · `Trajectory` · `State` (JSON ของ graph state ล่าสุดจาก checkpoint)

## 6. หน้า Portfolio (`/portfolio`)

```
┌ Main ──────────────────────────────────────────────┬ Inspector: AI Advisor ─┐
│ Summary tiles: มูลค่ารวม · เงินสด · P&L วันนี้ · P&L รวม  │ (Conversation component │
│   · จำนวน positions · base currency                │  ตัวเดียวกับ Research     │
│ Holdings table (แก้ไข inline):                      │  แต่ผูก portfolio session) │
│  Symbol | Qty | Avg cost | Price | Value | Weight%  │                         │
│  | Target% | P&L | P&L% | Sector | ⋯                │ ปุ่มด่วน:                  │
│  [+ เพิ่ม] [Import CSV] [กรอกแบบ % อย่างเดียว]          │  "ตรวจความเสี่ยงพอร์ต"      │
│ Allocation donut (by symbol / sector / asset type / │  "เสนอ rebalance"         │
│   currency) · Exposure bars                          │  "หาจุดอ่อน"              │
│ Risk panel: volatility · max drawdown · beta vs     │                         │
│   ^SET.BK/SPY · HHI concentration · correlation     │                         │
│   heatmap                                           │                         │
│ Rebalance proposal (เมื่อ advisor เสนอ):              │                         │
│  Symbol | Current% | Target% | Δ | Action | Qty est │                         │
│  policy results · [Approve] [Edit] [Reject]         │                         │
│ Approved plans history                              │                         │
└────────────────────────────────────────────────────┴─────────────────────────┘
```

- **ทุกตัวเลขในหน้านี้มาจาก `/portfolio/analytics`** (ซึ่ง compute ใน backend) frontend ห้ามคำนวณ weight/P&L เองนอกจาก format การแสดงผล
- hover ตัวเลขแล้วเห็น provenance เช่นเดียวกับหน้า Research
- ถ้าผู้ใช้กรอกแค่ % ให้แสดง badge "declared weights" และปิดคอลัมน์ Qty/P&L
- validation: รวม % ต้องไม่เกิน 100 (เตือนเมื่อ ≠ 100 และให้ถือส่วนต่างเป็นเงินสด), symbol ต้อง resolve ได้ และ quantity ต้องไม่เป็น 0
- **(P16) เพิ่มหุ้น (dialog) / วางข้อความ:** ปุ่ม `+ เพิ่ม` (และ "เพิ่ม position" ใน empty state) เปิด `AddPositionDialog` 2 ขั้น — (1) ค้นหา symbol แสดง badge ตลาด/สกุลเงิน + ราคาล่าสุด (`/market/{s}/quote`) (2) "คุณเห็นอะไรในแอปโบรกเกอร์?" เลือก 1 ใน 6 โหมด (`จำนวน + ต้นทุนเฉลี่ย` · `จำนวน + % กำไร/ขาดทุน` · `จำนวน + กำไร/ขาดทุน (บาท/USD)` · `มูลค่า + % กำไร/ขาดทุน` · `มูลค่า + กำไร/ขาดทุน (บาท/USD)` · `จำนวน + ต้นทุนรวม`, จำโหมดล่าสุด) กรอก 2 ช่อง + ปุ่มสลับ **กำไร / ขาดทุน** (เขียว/แดง) แทนการพิมพ์ลบ; preview card สด (debounce 400 ms → `…/positions/preview`) แสดงจำนวน · ต้นทุนเฉลี่ย · ราคาล่าสุด (as of) · มูลค่า · กำไร/ขาดทุน (เงิน + %) ที่ **backend คำนวณ** พร้อม provenance และบรรทัด "ระบบคำนวณจากราคาล่าสุด ค่าจะขยับตามตลาด"; error ของ backend แสดงที่ช่องนั้น; `เพิ่มอีกตัว` สะสมรายการ (แก้/ลบได้) แล้ว `บันทึกทั้งหมด` รวมกับ positions เดิม (symbol ซ้ำ = แทนแถวเดิม) แล้ว PUT — 422 กลับไปที่รายการตาม index; ลิงก์ "กรอกแค่ % น้ำหนัก" พาไปโหมด % ของตาราง. แถว draft ที่ยังอยู่ในตาราง (เช่น ออกจากโหมด %) เลือกโหมดจาก menu 6 โหมดเดียวกัน. ปุ่ม **วางข้อความจากแอปโบรกเกอร์** (empty state + toolbar) เปิด `PasteHoldingsDialog`: textarea (≤ 20k ตัวอักษร) + ตลาด (ไทย / สหรัฐฯ / ให้ AI เดา) → `POST /portfolios/parse-holdings` → ตารางแถวที่ AI อ่านได้ (แก้ symbol/โหมด/ตัวเลขได้, บรรทัดที่อ่านไม่ออกแสดงเป็นเตือน, `fallback_parsed` = แจ้งเบาๆ ให้ตรวจตัวเลข, `failed` = error) → preview ทุกแถว → `บันทึก` แบบ `เพิ่มต่อท้าย` (ค่าเริ่มต้น) หรือ `แทนที่ทั้งพอร์ต`; ไม่มี LLM (503) → "ตั้งค่า LLM ใน Settings ก่อน"

## 7. หน้า Settings (`/settings`)
- **LLM:** provider + model ของ deep/quick tier, ลำดับ failover และสถานะ key (บอกแค่มี/ไม่มี)
- **Data:** ลำดับ provider ต่อ capability (drag เรียงได้) และสถานะ key
- **Limits:** max iterations, tool call limit, timeout, budget ต่อ run
- **Policy:** max weight ต่อตัว, max sector, ต้องมี stop-loss, min confidence ที่ใช้ approve

## 8. Frontend architecture

```
frontend/
  app/(shell)/layout.tsx          # AppShell: Sidebar | main | Inspector
  app/(shell)/research/[[...id]]/page.tsx
  app/(shell)/trade/[[...id]]/page.tsx
  app/(shell)/portfolio/page.tsx
  app/(shell)/settings/page.tsx
  components/shell/               # AppShell, Sidebar, Inspector, DragHandle, CommandPalette, ProviderStatus
  components/conversation/        # Conversation, Turn, TurnProcess, ToolCallTree, ToolRow, ReasoningRow,
                                  # CompactionItem, WarningRow, GuardRow, Answer, CitationChip, StatsPills,
                                  # ContextMeter, Composer, ScenarioPicker, ApprovalPanel
  components/inspector/           # ScratchpadTab, ProvenanceTab, LineageTree, ContextTab, TrajectoryTab, StateTab
  components/trade/               # SymbolSearch, PriceChart, PipelineGraph, NodePanel, DebateView, DecisionCard
  components/portfolio/           # HoldingsTable, AllocationChart, RiskPanel, ProposalTable, SummaryTiles
  components/ui/                  # shadcn + StateDot, Kbd, JsonView
  lib/api.ts                      # typed fetch client (types generated from OpenAPI)
  lib/events.ts                   # EventEnvelope types (ตรงกับ contracts §10)
  lib/sse.ts                      # subscribeRun(runId, afterSeq, onEvent)
  stores/run-store.ts             # reducer: events → RunView (turns, calls tree, nodes, usage, approvals)
  stores/ui-store.ts              # layout widths, inspector tab, theme
```

**`run-store` คือหัวใจของ UI:** เป็น pure reducer `applyEvent(view: RunView, e: EventEnvelope) => RunView` ที่ test ด้วย fixture ของ event จริงได้ (อิง `ui-chat/src/client/chat/use-turn-data.ts`, `step-process.ts`)
- สร้าง tool tree จาก `call_id` / `parent_call_id`
- จัดกลุ่ม event ตาม node สำหรับ Trade
- `seq` ซ้ำหรือต่ำกว่าที่มีอยู่ให้ ignore (idempotent เพื่อให้ replay ได้)

Types ของ API ให้ generate จาก FastAPI OpenAPI ด้วย `openapi-typescript` (สคริปต์ `pnpm gen:api`) ส่วน event types ให้เขียนมือใน `lib/events.ts` ตาม contracts และมี test เทียบกับ JSON schema ที่ backend export

## 9. Empty / error / loading states (ต้องมีทุกหน้า)
- **Research:** empty = การ์ด scenario ให้กดเริ่มได้ทันที 4–6 ใบ
- **Trade:** empty = ช่องค้นหา symbol + ตัวอย่าง (PTT, AAPL, EURUSD, XAUUSD)
- **Portfolio:** empty = ปุ่ม "เพิ่ม position", "Import CSV" และ "กรอกแบบ %"
- SSE หลุด: แสดงแถบ "กำลังเชื่อมต่อใหม่…" แล้วต่อใหม่ด้วย `Last-Event-ID` อัตโนมัติ
- Provider ไม่มี key: แสดง tooltip บอกว่าต้องใส่ env ตัวไหน (อ่านจาก `/config/status`)
