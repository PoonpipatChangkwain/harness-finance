# Phase 11 — Trading UI → MVP #2

## เป้าหมาย
หน้า `/trade` ตาม [ui-spec.md](../ui-spec.md) §5: ค้นหา symbol → ดูกราฟราคา → ตั้งค่าแล้วกด Run → เห็น **pipeline graph ทำงานสด** → คลิก node ดู transcript/tool calls → เห็น debate → decision card → approve/reject/edit → resume ได้เมื่อล้ม

## ต้องเสร็จก่อน
P08, P10

## อ่านก่อนเริ่ม
- `docs/ui-spec.md` §5, §8
- `../deepseek-harness/packages/client/ui-workflow-run/src/client/WorkflowRunPanel.tsx`, `workflow-definition.ts` (สถานะ run/phase/member)
- `../deepseek-harness/packages/client/ui-subagent/src/client/` (แนวคิดการเปิด transcript ของ sub-agent)
- `../TradingAgents/cli/` (ดูว่า CLI แสดง progress ของ agent แต่ละตัวอย่างไร เช่น layout แบบ live table)
- เอกสาร `lightweight-charts` (v5: `addSeries(CandlestickSeries)`, panes) และ `@xyflow/react` (custom nodes, `dagre` auto-layout)

## Tasks

### 11.1 Dependencies
`lightweight-charts`, `@xyflow/react`, `@dagrejs/dagre`

### 11.2 Components (`components/trade/`)
- `SymbolSearch`: combobox (shadcn Command) debounce 250ms ไปที่ `/symbols/search` แสดง badge ประเภท (stock·TH, stock·US, forex, crypto, commodity) และจำรายการล่าสุด 10 ตัวใน localStorage
- `PriceChart`:
  - candlestick + volume histogram จาก `/market/{symbol}/ohlcv`
  - overlays SMA20/50/200 และ Bollinger (ขอค่า indicator จาก backend endpoint ใหม่ `GET /api/market/{symbol}/indicators?set=sma20,sma50,bb20,rsi14,macd` ที่เรียก compute **ห้ามคำนวณใน frontend**)
  - pane RSI/MACD ที่เปิดปิดได้, ปุ่ม range 1M/3M/6M/1Y/5Y และเส้น entry/stop/target จาก decision (price lines) เมื่อ run มี decision
  - รองรับ dark/light ผ่าน tokens และ resize observer
- `RunSetupBar`: form (react-hook-form + zod) ตาม `TradeRunInput` ที่ปรับ analysts ตาม asset type อัตโนมัติ (forex ปิด fundamentals และเปิด macro) และมี date picker (default วันนี้; ถ้าวันในอดีตให้แสดง badge "backtest mode: ข้อมูลถูกตัดตาม as_of")
- `PipelineGraph`:
  - โหลดโครงจาก `/workflows/trading/graph` (กรอง node ตาม analysts ที่เลือก) แล้ว layout ด้วย dagre (LR)
  - custom node: StateDot + label + duration + badge จำนวน tool calls + ไอคอน 💾 เมื่อ checkpoint.saved และขอบอำพันกะพริบเมื่อ waiting_approval
  - edge แบบ conditional เป็นเส้นประพร้อม label ส่วน edge ที่ถูกใช้จริงให้ไฮไลต์
  - debate node แสดง "รอบ 2/3" และสถานะมาจาก `run-store.nodes` (reducer ใน P08 ที่รองรับ node events)
  - คลิก node แล้วตั้ง `selectedNode` แล้วเปิด inspector tab Node
- `NodePanel` (inspector tab "Node"): หัวข้อ node + status + duration + tokens/cost ของ node, reasoning + `ToolCallTree` (ใช้ component จาก P08 โดยกรอง calls ตาม `node`), report markdown และ structured output (JSON viewer + rendered)
- `DebateView` (inspector tab "Debate"): คอลัมน์ Bull | Bear หรือ Aggressive | Conservative | Neutral ข้อความเรียงตามรอบ และคำตัดสินของ judge ด้านล่าง
- `DecisionCard`: Rating badge, Verdict badge (สีตาม `--fh-verdict-*`) + confidence bar, entry/stop/targets/R:R/size (ทุกตัวมี CitationChip → provenance), เหตุผลย่อ และ policy results (✓/✗ พร้อมข้อความ)
- `TradeApprovalPanel`: ต่อยอด ApprovalPanel (P08) ด้วยฟอร์ม Edit (แก้ size/stop/targets) ส่ง `edited` ไปแล้วแสดงผล policy ที่คำนวณใหม่จาก event ที่ตามมา
- `ResumeBanner`: เมื่อ run `failed/interrupted` และ `resumable` ให้แสดง "ล้มที่ node X — Resume จาก checkpoint" แล้ว `POST /runs/{id}/resume`
- `StateTab`: JSON ของ `/runs/{id}/state`

### 11.3 Page `/trade/[[...runId]]`
- sidebar: history จาก `/trade/runs` (symbol · date · rating · verdict · StateDot) พร้อม filter ตาม symbol
- ไม่มี runId: แสดง SymbolSearch + ตัวอย่าง (PTT, AAPL, EURUSD, XAUUSD, BTCUSD) และเมื่อเลือกแล้วแสดง chart + setup bar
- มี runId: chart + graph + decision และ subscribe events (replay)
- Inspector tabs: Node · Debate · Scratchpad · Provenance · Trajectory · State (ใช้ของ P08 ซ้ำ)

### 11.4 Tests
- reducer: fixture ของ trading run จริง (อัดจาก backend ด้วย FakeLLM) แล้วเช็กสถานะ node, ลำดับ debate และ decision
- PipelineGraph render ด้วย node ตาม analysts ที่เลือก และสถานะเปลี่ยนตาม event
- DecisionCard citation และ policy ✗ แสดงถูกต้อง
- Approval edit → request body ถูกต้อง
- ResumeBanner แสดงเมื่อ resumable

## Definition of Done
- [ ] เลือก PTT → Run → เห็น 4 analyst node กระพริบพร้อมกัน → debate → … → approval ขึ้นแทน composer → Approve → report
- [ ] เลือก EURUSD แล้ว fundamentals ไม่อยู่ใน graph
- [ ] ฆ่า backend ระหว่าง risk debate → เปิดใหม่ → ResumeBanner → Resume ทำงาน
- [ ] ราคาใน chart และตัวเลขใน decision card ตรงกับ provenance
- [ ] lint/typecheck/test ผ่าน

## นอกขอบเขต
หน้า Portfolio
