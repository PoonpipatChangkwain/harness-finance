# Phase 13 — Portfolio UI + Settings → MVP #3

## เป้าหมาย
หน้า `/portfolio` ตาม [ui-spec.md](../ui-spec.md) §6: กรอกหรือ import พอร์ต, ดู analytics และ risk, คุยกับ AI Advisor ใน inspector, ดู rebalance proposal แล้ว approve/edit/reject และ apply แผน
หน้า `/settings` ตาม §7

## ต้องเสร็จก่อน
P08, P12 (P11 สำหรับ ApprovalPanel แบบ edit)

## อ่านก่อนเริ่ม
- `docs/ui-spec.md` §6, §7, §9
- `app/api/routes/portfolio.py` (P12) และ OpenAPI types
- `../deepseek-harness/packages/client/ui-settings-*/` (โครงหน้า settings: models, agent-loop, web-search, plugins)

## Tasks

### 13.1 Dependencies
`recharts`, `@tanstack/react-table`, `papaparse` (preview CSV ฝั่ง client ก่อนส่ง)

### 13.2 Components (`components/portfolio/`)
- `SummaryTiles`: มูลค่ารวม, เงินสด, P&L วันนี้, P&L รวม, จำนวน positions และ base currency โดยตัวเลขมาจาก analytics พร้อม CitationChip และใช้สี up/down
- `HoldingsTable` (tanstack):
  - คอลัมน์ตาม ui-spec แก้ inline ได้ (qty, avg cost, target%, note) และ symbol ใช้ SymbolSearch
  - แถวใหม่อยู่ในสถานะ draft ได้ แล้วกด "บันทึก" เพื่อ PUT และแสดง error 422 ที่แถวที่ผิด
  - โหมด "กรอกแบบ % อย่างเดียว" จะซ่อน qty/cost/P&L และแสดงผลรวม % แบบสด (เตือนเมื่อ ≠ 100 และบอกว่าส่วนต่างถือเป็นเงินสด)
  - sort, filter และ column visibility ได้ ตัวเลขใช้ `tabular-nums`
  - ค่าในคอลัมน์ Price/Value/Weight/P&L มาจาก analytics เท่านั้น
- `ImportCsvDialog`: drop file → preview ด้วย papaparse → `POST /portfolio/import` (preview) → แสดง errors → Commit
- `AllocationChart`: donut (Recharts) สลับมุมมอง symbol/sector/asset type/currency พร้อม legend แบบตาราง
- `ExposureBars`: แท่งแนวนอนต่อ sector เทียบกับ limit ของ policy (เส้นแดง)
- `RiskPanel`: vol, MDD, beta, VaR/CVaR, HHI และ effective N + `CorrelationHeatmap` (grid สีแบบ diverging พร้อม tooltip) + ตาราง contribution to risk
- `ProposalTable`: symbol | current% | target% | Δ | action (Buy/Sell/Hold) | qty est. | มูลค่า พร้อม metric ก่อน → หลัง, policy results และ Approve/Edit/Reject (ใช้ ApprovalPanel แบบ edit จาก P11 โดยแก้ target% ได้)
- `PlansHistory`: รายการ plan (approved/applied) พร้อมปุ่ม "Apply to portfolio" (ต้องยืนยันด้วย dialog ที่แสดง diff ของ positions) และปุ่มดู revision
- `AdvisorPanel` (inspector): ใช้ `Conversation` ของ P08 กับ session `kind=portfolio` มี quick actions 3 ปุ่ม และเมื่อ advisor run ถึง approval ให้เลื่อน main ไปที่ ProposalTable พร้อม highlight

### 13.3 Page `/portfolio`
- โหลด `/portfolio` + `/portfolio/analytics` (มี skeleton ระหว่างโหลด) และ refresh analytics หลังบันทึกหรือทุก 60s เมื่อหน้าถูกเปิดอยู่
- empty state ตาม ui-spec §9
- warnings จาก analytics แสดงเป็น banner (เช่น "ดึงราคา XYZ ไม่ได้")

### 13.4 Settings `/settings` + backend
- backend: `GET/PUT /api/settings` เก็บในตาราง `settings` (override ค่าใน env/defaults ได้แค่ส่วนที่อนุญาต) และ **ห้ามรับหรือคืน API key ผ่าน API นี้** ให้แสดงแค่ว่า key มีหรือไม่
- sections:
  1. **LLM:** เลือก provider/model ของ deep และ quick (รายการจาก model_catalog) + ลำดับ failover (drag)
  2. **Data providers:** ต่อ capability มี list ที่ drag เรียงได้ พร้อมสถานะ configured และ tier
  3. **Harness limits:** max iterations, tool call limit, similarity threshold, tool timeout, run budget $ และ compaction thresholds (แสดงค่า default ของ Dexter ข้างๆ)
  4. **Policy:** max position %, max sector %, max risk per trade %, min confidence, require stop-loss, max turnover
  5. **Appearance:** theme และขนาดฟอนต์
- ปุ่ม "Reset to defaults" ต่อ section

### 13.5 Tests
- HoldingsTable: แก้ค่า → PUT body ถูกต้อง, 422 แสดงที่แถว และโหมด % รวมไม่ครบ 100 ขึ้นเตือน
- ImportCsvDialog preview/errors
- ProposalTable edit → approval body
- Settings: drag reorder → PUT body และค่า key ไม่เคยอยู่ใน response (ตรวจ mock)
- reducer/fixture ของ advisor run

## Definition of Done
- [x] กรอกพอร์ต 5 ตัว (ไทย + US + เงินสด) แล้วเห็น tiles, donut, risk, heatmap ครบ โดยทุกตัวเลข hover แล้วเห็น provenance
- [x] กด "เสนอ rebalance" → เห็น process ใน AdvisorPanel → ProposalTable → Edit target → policy คำนวณใหม่ → Approve → Apply → holdings เปลี่ยนพร้อม revision
- [x] เปลี่ยนลำดับ provider ใน Settings แล้ว run ถัดไปใช้ลำดับใหม่ (ดูจาก provenance.source)
- [x] lint/typecheck/test ผ่าน

> ตรวจแล้ว 2026-09-26 บน live server (yfinance + Groq, data dir ชั่วคราว): พอร์ต 5 ตัว (PTT, KBANK, AAPL, MSFT, NVDA แบบมูลค่า+% กำไร) + เงินสด → analytics/risk/heatmap ครบ; advisor ครั้งแรกถูก policy `max_sector_weight` block 2 รอบ → rejected (ถูกต้อง), ครั้งที่สอง → approval → edit target → คำนวณ + policy ใหม่ผ่าน → plan → apply → revision `apply_plan`; reorder `statements` เป็น yfinance ก่อน → run ถัดไป provenance.source เปลี่ยนจาก `sec_edgar` เป็น `yfinance`. หน้าเว็บตรวจด้วย screenshot headless Edge (render หลัง fetch เสร็จ) — การคลิก/hover ใน browser ครอบคลุมด้วย component test

### 13.6 คำขอเพิ่มจากผู้ใช้ (2026-09-26) — ดู P12 §12.7
- หน้า `/portfolio` แบบ Dime!: toggle **แยกสินทรัพย์ / แยกพอร์ต**, chips กรองพอร์ต/ตลาด (ทั้งหมด · ไทย · สหรัฐฯ), การ์ดมูลค่ารวม (ทั้ง USD และ ≈THB + อัตรา FX, ต้นทุนรวม, % เปลี่ยนจากวันก่อน, กำไรที่ถืออยู่), donut สัดส่วนตามพอร์ต, รายการพอร์ต (ชื่อ, จำนวนสินทรัพย์, มูลค่า, กำไร) และรายการสินทรัพย์ (โลโก้/ตัวย่อ, % น้ำหนัก, มูลค่า + ≈THB, % และจำนวนกำไร สีเขียว/แดง) พร้อม sort ตามมูลค่า/กำไร
- ฟอร์มเพิ่มสินทรัพย์แบบ "มูลค่า + % กำไรขาดทุน" (และแบบ qty/avg cost เป็นทางเลือก) + สร้าง/แก้/ลบพอร์ต
- ส่วน "รายงาน AI รายสัปดาห์" (ศุกร์ 03:00) + ปุ่มรันตอนนี้
- Research Composer: toggle "ใช้ข้อมูลพอร์ต" + เลือกพอร์ต (ส่ง `portfolio_ids`) และ chip บน turn ว่าใช้พอร์ตไหน

## นอกขอบเขต
broker sync
