# Phase 08 — Frontend shell + Research Chat UI (แบบ deepseek-harness) → MVP #1

## เป้าหมาย
สร้าง App shell 3 คอลัมน์ และหน้า Research Chat ที่ **เห็น harness ทำงานทุกขั้น**: tool call tree, reasoning, compaction, context meter, guard/warning, citations และ inspector (Scratchpad, Provenance, Context, Trajectory) ตาม [ui-spec.md](../ui-spec.md) §3, §4 และ §8

## ต้องเสร็จก่อน
P07 (สามารถเริ่ม shell + reducer คู่ขนานได้ตั้งแต่ P05 โดยใช้ echo run)

## อ่านก่อนเริ่ม
- `docs/ui-spec.md` ทั้งไฟล์, `docs/contracts.md` §10, §11
- deepseek-harness client (ดูพฤติกรรม ไม่ลอกโค้ด framework):
  - `packages/client/ui-layout/src/client/AppFrame.tsx`, `columns.ts`
  - `packages/client/ui-chat/src/client/chat/ChatView.tsx`, `TurnProcessNodeView.tsx`, `ReasoningRow.tsx`, `CompactionItem.tsx`, `StatsPills.tsx`, `TurnUsagePanel.tsx`, `TurnNavigator.tsx`, `step-process.ts`, `use-turn-data.ts`, `use-scroll-follow.ts`, `token-format.ts`
  - `packages/client/ui-tool/src/client/tool/ToolCallTree.tsx`, `components/ToolRow.tsx`, `ToolDetails.tsx`, `PreparingToolRow.tsx`, `models/tool-call-model.ts`
  - `packages/client/ui-approval/src/client/ApprovalPanel.tsx`
  - `packages/client/ui-conversation/src/client/context-occupancy.ts`
  - `packages/client/ui-trajectory/src/client/TrajectoryView.tsx`, `TrajectoryTable.tsx`, `TrajectoryTimeline.tsx`, `timeline.ts`
  - `packages/client/ui-sidebar-right/src/client/shell/SidebarRight.tsx`
  - `packages/client/ui-theme/src/styles/base.css`
- `../dexter/src/components/tool-event.ts`, `working-indicator.ts`, `debug-panel.ts` (ข้อมูลที่ Dexter แสดงใน CLI)

## Tasks

### 8.1 Infrastructure
- `lib/events.ts`: types ตาม contracts §10 และ test ที่โหลด `backend/schemas/events.schema.json` แล้วตรวจว่า fixture event ผ่าน schema
- `lib/sse.ts`: `subscribeRun(runId, {afterSeq, onEvent, onStatus, signal})` ใช้ fetch-event-source พร้อม auto-reconnect (backoff 1s→10s) ส่ง `Last-Event-ID` เสมอ และส่ง status `connecting | open | reconnecting | closed` ไปที่ UI
- `pnpm gen:api` → `lib/api-types.ts` แล้วทำ `lib/api.ts` เป็น typed client (sessions, messages, runs, scratchpad, provenance, skills, config/status)
- `stores/run-store.ts`: pure reducer `applyEvent(view, e)` → `RunView`:
  ```ts
  RunView = { runId, status, seq, startedAt, finishedAt?, workflow,
    turns: Turn[] /* research */, nodes: Record<string, NodeView> /* trade */,
    calls: Record<callId, CallView>, rootCallIds: string[], // tree
    reasoning: Segment[], answer: { markdown, citations, unverified } | null,
    context: { used, window, percent, compactions: CompactionView[] },
    warnings: WarningView[], limits: LimitView[], approvals: Record<id, ApprovalView>,
    usage: { in, out, cost, llmCalls, toolCalls, durationMs } }
  ```
  - idempotent: event ที่ `seq <= view.seq` ให้ ignore
  - `useRunStore(runId)` (Zustand) เก็บหลาย run และ subscribe อัตโนมัติ
- `stores/ui-store.ts`: ความกว้างคอลัมน์, inspector เปิด/ปิด, tab, theme (persist ใน localStorage ห่อด้วย try/catch)

### 8.2 App shell (`components/shell/`)
- `AppShell` แบ่ง grid 3 คอลัมน์ + `DragHandle` (pointer capture, rAF throttle, clamp min/max ตาม `columns.ts`) และ responsive breakpoints ตาม ui-spec §3
- `Sidebar`: nav (Research/Portfolio/Trade/Settings), รายการ session (`GET /sessions?kind=research`) ที่มี title, เวลา, StateDot ของ run ล่าสุด และปุ่ม "+ New"
- `ProviderStatus`: จุดสีต่อ provider/LLM จาก `/config/status` และ tooltip บอก env ที่ขาด
- `Inspector`: tabs แบบ registry (แต่ละหน้าลงทะเบียน tab ของตัวเอง ตามแนวคิด `tab-registry.ts`)
- `CommandPalette` (`Ctrl+K`): ไปหน้าต่างๆ, เปิด session และค้น symbol
- theme toggle system/light/dark

### 8.3 Conversation components (`components/conversation/`)
- `Conversation` = header + transcript (virtualized เมื่อเกิน 50 turns) + composer และ scroll-follow แบบ dsh (ติดท้ายเมื่อผู้ใช้อยู่ล่างสุด หยุดตามเมื่อผู้ใช้เลื่อนขึ้น และมีปุ่ม "↓ ล่าสุด")
- `Turn`: user bubble (+ scenario chip) → `TurnProcess` → `Answer` → `StatsPills`
- `TurnProcess`: หัวข้อ "Process · N steps · M tools · 12.3s" เปิดอัตโนมัติระหว่าง run และยุบเมื่อเสร็จ (จำสถานะต่อ turn) ภายในเรียงตาม seq ได้แก่ ReasoningRow, ToolCallTree, CompactionItem, WarningRow, GuardRow และ node header (ถ้ามีหลาย node เช่น plan/verify)
- `ToolCallTree` + `ToolRow` + `ToolDetails` ตาม ui-spec §4.1 (icon ตาม category, summary, StateDot, duration, shimmer ของ progress, error summary, กางแล้วเห็น Args/Output/Provenance/meta, เส้น batch concurrent และ subcalls ซ้อน)
- `ReasoningRow`: ยุบเหลือ 2 บรรทัด กดเพื่อกาง และแสดงแบบ streaming
- `CompactionItem`: สำหรับ full compaction (กดแล้วกาง summary) และ microcompact (บรรทัดจาง) และ fallback_clear (สีอำพัน)
- `GuardRow`/`WarningRow`: ไอคอน + ข้อความ + รายละเอียด
- `Answer`: react-markdown + `CitationChip` (hover แสดง popover provenance, click เปิด inspector Provenance ที่ record นั้น) และขีดเส้นใต้หยักที่ตัวเลข unverified พร้อม tooltip อธิบาย
- `StatsPills`: in/out tokens (format แบบ `token-format.ts`: 1.2k, 3.4M), cost, duration, จำนวน LLM calls และ hover แล้วเห็น breakdown ตาม purpose
- `ContextMeter` ใน header ตาม ui-spec (สีตามระดับ และ tooltip มี threshold)
- `Composer`: textarea auto-grow (Enter ส่ง, Shift+Enter ขึ้นบรรทัด), `ScenarioPicker` (จาก `/skills` แสดง unavailable เป็นสีเทาพร้อมเหตุผล), ปุ่ม Send/Stop และ disabled เมื่อ SSE ไม่ connected
- `ApprovalPanel` (takeover composer): สร้างไว้ตอนนี้แต่ใช้จริงใน P09 โดยมีคีย์ลัด Enter/Esc ตาม dsh

### 8.4 Inspector tabs (`components/inspector/`)
- `ScratchpadTab`: list entries (type badge, tool, time, duration), filter ตาม tool/type, กางเป็น JSON และปุ่ม download `.jsonl`
- `ProvenanceTab`: ตาราง (tanstack) ที่ sort/filter ได้ + `LineageTree` (recursive, lazy load ผ่าน `?lineage=1`) ที่ scroll ไปหา record ที่เลือกจาก citation
- `ContextTab`: gauge + รายการ compaction (ก่อน → หลัง, เวลา, summary) + จำนวน active/cleared tool results
- `TrajectoryTab`: ตาราง event (seq, t+ms, type, node, tool, duration) + `TrajectoryTimeline` (bar แนวนอนต่อ call ตามช่วงเวลาจริงที่เห็นการซ้อนกันของ concurrent) และ filter ตาม type

### 8.5 Page `/research/[[...id]]`
- ไม่มี id: แสดง empty state การ์ด scenario (ui-spec §9) และกดแล้วสร้าง session + เติม scenario ใน composer
- มี id: โหลด session (messages + runs) → render turns เก่าจาก events ของแต่ละ run (replay จาก seq 0) → subscribe run ที่ยังไม่จบ
- URL เปลี่ยนเมื่อสร้าง session ใหม่ (`router.replace`)

### 8.6 Tests
- reducer: fixture event streams จริงที่อัดจาก backend (`frontend/tests/fixtures/research-run-*.jsonl`) อย่างน้อย 4 แบบ ได้แก่ ปกติ, concurrent tools + subcall, compaction + guard limit และ error/cancel แล้ว snapshot `RunView`
- idempotency: ป้อน event ซ้ำ/สลับลำดับแล้ว state ต้องเท่าเดิม
- component: ToolRow (running/done/error/expanded), CompactionItem, CitationChip popover และ ContextMeter สีตาม %
- sse: reconnect ส่ง Last-Event-ID ที่ถูกต้อง (mock fetch)

## Definition of Done
- [ ] เปิด `/research` เลือก "Earnings Review" แล้วพิมพ์ "AAPL" จะเห็น process ทำงานสด (tool rows ขึ้นทีละตัว, concurrent เห็นเส้น batch), context meter ขยับ, คำตอบมี citation chips และ hover เห็น provenance
- [ ] กด Stop ระหว่าง run ได้ status cancelled และคำตอบ graceful
- [ ] refresh หน้าระหว่าง run แล้ว UI กลับมาครบ (replay) และต่อ live ต่อได้
- [ ] Inspector ทั้ง 4 tabs ใช้งานได้กับ run จริง
- [ ] ปิด backend แล้วเห็นแถบ "กำลังเชื่อมต่อใหม่…" และเมื่อเปิดใหม่ก็ต่อได้เอง
- [ ] ใช้งานได้ที่จอกว้าง 1440 / 1024 / 390 px
- [ ] lint/typecheck/test ผ่าน

## นอกขอบเขต
หน้า Trade/Portfolio และการใช้ ApprovalPanel จริง
