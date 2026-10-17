# Phase 17 — Live verification log (2026-10-17)

ตรวจ Definition of Done ของ [phase-17-advisor-memory.md](phase-17-advisor-memory.md) บน server จริง + LLM จริง ผ่าน API (`curl` / httpx script) — ไม่ได้คลิก UI ใน browser

**Setup**
- backend: `uv run python -m uvicorn app.api.main:app --port 8000` จาก `Product/backend/` (DATA_DIR = `backend/backend/.data`, migration 0006 อยู่แล้ว, memory/RB ว่างตอนเริ่ม)
- LLM ตาม `backend/.env`: **deep = groq `openai/gpt-oss-120b`**, **quick = ollama_cloud `gemma4:31b`** (failover google) — memory ใช้ tier quick ทั้งหมด
- frontend `pnpm dev` (:3017): `/memory`, `/research`, `/research/<ses>`, `/portfolio`, `/trade` ตอบ 200, log ไม่มี error (ไม่ได้ดู browser console)

## 1. Memory supersede — ✅ ผ่าน

| ขั้น | Request | ผล |
|---|---|---|
| 1 | `POST /sessions/ses_01M557XM…/messages {"text":"ผมชอบลงทุนเรื่อย ๆ ระยะยาว ไม่รีบ"}` | events: `node:plan → recall_memory → memory.recalled{items:[]} → assess_discovery → discovery.assessed{whys_asked:0} → retrieve_analyze → verify → synthesize_report → answer.final{discovery:null} → learn → memory.updated{ADD investment_style}` |
| | `GET /memory?status=all` | `mem_01M557YD…` `investment_style` key `pace` "ผู้ใช้ชอบลงทุนแบบค่อยเป็นค่อยไปและถือระยะยาว ไม่ชอบความเร่งรีบ" **active**, source `user_stated` 0.9 |
| 2 | `{"text":"ตอนนี้ไม่ชอบแบบเรื่อย ๆ แล้ว ชอบเร็ว ๆ เทรดสั้น"}` | `memory.recalled` มี item เดิม (FTS); `memory.updated{SUPERSEDE id=mem_01M557ZX… previous_text="…ค่อยเป็นค่อยไป…"}` (key ต่างกัน `pace`→`style.primary` จึงผ่าน reconcile LLM — memory 3 calls) |
| | `GET /memory?status=all` | เก่า `superseded`, `superseded_by=mem_01M557ZX…`, `valid_to=2026-10-17T15:34:21Z`; ใหม่ `active`, `supersedes=` เก่า |
| | `GET /memory/profile` | มีแค่ "ผู้ใช้ไม่ชอบการลงทุนแบบค่อยเป็นค่อยไป และเปลี่ยนมาชอบการลงทุนที่รวดเร็วและเน้นการเทรดระยะสั้น" (67 tokens) |
| | `GET /memory/mem_01M557YD…/history` | chain เก่า→ใหม่ 2 แถว พร้อม valid_from/valid_to |

## 2. 5 Whys — ✅ ผ่าน (turn 2 เป็น `research_then_ask` ไม่ใช่ `ask_only`)

Session `ses_01M5580PGQRA83EK877VMVW7EQ`

| Turn | ข้อความ | discovery.assessed | answer.final |
|---|---|---|---|
| 1 | "ผมอยากซื้อ NVDA" | whys 1, conf 0.0, next_question ✓ | ข้อมูลราคา/SMA/RSI/DCF/consensus **8 citations, unverified 0**, จบด้วยคำถามเดียว "…ถ้าได้ลงทุนในตัวนี้แล้ว จะช่วยให้เป้าหมายการเงินของคุณเป็นจริงได้อย่างไรครับ?"; `discovery={whys_asked:1,max:5,question:…}`; คำตอบใช้โปรไฟล์ ("แนวทางสำหรับสายเทรดระยะสั้น"); 203 s |
| 2 | "มันขึ้นเยอะ กลัวตกรถ" | whys 2, conf 0.3 | research_then_ask: 3 citations + คำถาม 2/5; `memory.updated{ADD behavioral_pattern "…(FOMO)" (inferred)}`; session chain `[{q1,a:"มันขึ้นเยอะ กลัวตกรถ"},{q2,a:""}]` |
| 3 | "ถ้าเสียก็ไม่กระทบมาก เป็นเงินเย็น ผมอยากให้เงินก้อนนี้โตไว ๆ เพื่อเก็บเป็นเงินดาวน์คอนโดภายใน 3 ปี" | **root_goal_identified=true**, stop `root_goal`, `root_goal={text:"เก็บเป็นเงินดาวน์คอนโดภายใน 3 ปี",category:"home"}` | 4 citations, `discovery:null`; `memory.updated` มี goal ADD; `reasoning.distilled{rb_01M558BT… "Align high-volatility assets with time horizons"}`; `GET /sessions/{id}.discovery` → `whys_asked:0, chain:[]`, root_goal คงไว้ |

- **Skip:** session ใหม่ `ses_01M558EZ…` `{"text":"ผมอยากซื้อ AAPL","skip_discovery":true}` → `discovery.assessed{stop_reason:"resistance", next_question:null}`, `answer.final.discovery=null`, คำตอบ 12 citations ไม่มีคำถามปิดท้าย และใช้ memory (แยกเงินดาวน์คอนโด 3 ปีออกจาก AAPL, เตือน FOMO); memory calls = 1 (extract)
- **พบบั๊ก → แก้แล้ว:** turn 3 เขียน goal ซ้ำ 2 แถว (`goal:condo_downpayment` "…ภายในระยะเวลา 3 ปี" + `goal:condo_2029` "…ภายในปี 2029") และ risk_capacity ซ้ำ 2 แถว เพราะ `memory_candidates` ของ discovery กับผล extract (ที่เห็น root goal เป็น context) บอกเรื่องเดียวกันด้วย key ต่างกัน → `harness/memory/engine.py::learn` ให้หมวดของ discovery candidates เป็นของ root goal ใน batch นั้น (ทิ้ง extracted fact หมวดเดียวกัน) + test `test_discovery_facts_own_their_category_in_the_batch`; แถวซ้ำที่เกิดก่อนแก้ยังอยู่ใน dev DB
- Model quality (ไม่ใช่บั๊ก): turn 3 reconcile SUPERSEDE สไตล์เป็น "ผู้ใช้ต้องการให้เงินลงทุนเติบโตอย่างรวดเร็ว" (เสียรายละเอียด "เทรดสั้น"); turn 1 มี marker citation หลุดรูป เช่น "$179.46[3]"

## 3. Advisor + ReasoningBank — ✅ ผ่าน (บนพอร์ตทดสอบชั่วคราว)

- dev DB **ไม่มี `pf_default`**; พอร์ตจริงตัวเดียวคือ `pf_01M3F8JRFYTPDMG97JH87E55VD` "พอร์ตเติบโต VI" (13 positions, cash null, จำนวนหุ้นเป็นเศษ) → `POST /portfolios/pf_01M3F8…/advise {}`: `memory.recalled` (goal + risk_capacity), report มี `_Goal from your memory: ผู้ใช้มีเป้าหมายเก็บเงินเพื่อดาวน์คอนโดภายในปี 2029_` และ diagnosis อ้าง FOMO/เป้าหมายคอนโด — แต่แผน 0 หุ้นทุกตัว (เศษหุ้น + ไม่มีเงินสด) → policy block 2 รอบ → rejected ก่อนถึง approval (พฤติกรรมเดิมของ P12) → `learn` ไม่ทำอะไรตามสเปก
- จึงสร้างพอร์ตทดสอบ `pf_01M558WB…` (USD, cash 4000, 8 ตัวเป็นจำนวนเต็ม, tech < 40%) — รอบแรก tech เกินกรอบ 40.53% หลังปัด lot → block; ปรับพอร์ตแล้ว:
  - run `run_01M55937…` → `approval.required` (4 trades) → `POST /approvals/apr_01M55966… {"outcome":"approved"}` → `save_plan → report → learn → reasoning.distilled` 2 items; memory calls 3 (distill + privacy judge × 2)
  - run ซ้ำ `run_01M55986…` → state `memory.reasoning` = ทั้ง 2 items ที่เพิ่งกลั่น → approve → `use_count 0→1`, `success_count 1`, `success_rate 0.5→0.667`; `GET /reasoning-bank/{id}.uses` = `[{id:"rbu_…", run_id:…F7JBJ1, outcome:"success"}]`; กลั่นเพิ่มอีก 2 items
  - ลบพอร์ตทดสอบหลังตรวจเสร็จ (`DELETE /portfolios/pf_01M558WB…`)
- **ตรวจ privacy ด้วยตา (5 items ใน bank):** ไม่มีชื่อหุ้น จำนวน ต้นทุน มูลค่า หรือข้อความของผู้ใช้
  - *De-risk winning positions for fixed-date goals* — "Applies when a portfolio has high unrealized gains in concentrated positions and a hard deadline (e.g., < 5 years)… lock in profits from concentrated winners… rebalance into defensive sectors and increase cash reserves"
  - *Identify risk concentration beyond simple weighting* — risk contribution vs weight, มองกลุ่ม "multiple big-tech names" ที่ correlate กันเป็นก้อนเดียว, ใช้ SMA50/200 เป็น tripwire
  - *Decluster correlated assets beyond sector labels* / *Detect hidden risk in missing price data* (ใช้ proxy volatility เมื่อ holding ">10% of portfolio" ไม่มีราคา) / *Align high-volatility assets with time horizons* (research: แยกเงิน goal-specific กับ speculative)
  - dedupe ไม่รวม "Decluster…" กับ "Identify risk concentration…" (ความหมายใกล้กันแต่ trigram ต่าง — รอ EmbeddingPort)

## 4. Forget — ✅ ผ่าน

- ก่อนลบ: chain `mem_01M557YD…`(superseded) → `mem_01M557ZX…`(superseded) → `mem_01M558BKE84F…`(active); FTS `MATCH "ค่อยเป็นค่อยไป"` (อ่าน `user_memory_fts` แบบ read-only) เจอ 2 แถว
- `DELETE /memory/mem_01M557YD…?cascade=1` → `{"deleted":[3 ids]}` (cascade ลบทั้ง chain รวมหัวที่ active)
- หลังลบ: `GET /memory?status=all` ไม่มีทั้ง 3 · `GET /memory/{old}/history` → 404 · ตารางหลัก + FTS = 0 แถวทั้ง 3 id, FTS `ค่อยเป็นค่อยไป` → `[]` · `/memory/profile` ไม่มีแล้ว (`/memory` ไม่มี `?q=` จึงตรวจ FTS ที่ DB ตรง ๆ; tool `user_memory_search` ใช้ `search_fts` ตัวเดียวกัน)

## 5. Overhead ของ memory ต่อ turn (usage `by_purpose.memory`, quick tier)

| กรณี | memory LLM calls | tokens in/out (ตัวอย่าง) |
|---|---|---|
| research, ไม่มีอะไรต้อง reconcile (assess + extract) | 2 | 2.8k / 0.2k – 5.7k / 0.4k |
| research + reconcile 1 fact | 3 | 3.8k / 0.4k |
| research, root goal identified (assess + extract + distill + privacy judge) | 4 | 10.4k / 1.7k |
| research `skip_discovery` (extract อย่างเดียว) | 1 | 4.0k / 12 |
| ask-only turn | + 1 `final` (write_question) แทน agent/final ทั้งชุด — ถูกลง | — |
| advisor approve (distill + privacy judge × 2) | 3 | 4.8k / 0.9k |
| `recall_memory` | 0 (SQLite/FTS เท่านั้น) | — |

เทียบกับงานหลักต่อ turn (agent 1–4 calls, 10k–67k input tokens) memory เพิ่ม ~2 quick calls ≈ 5–15% ของ input tokens; เวลาเพิ่มราว 3–8 s ต่อ turn (gemma4:31b บน Ollama Cloud)

## สิ่งที่ยังไม่ได้ตรวจ / ข้อสังเกต
- ไม่ได้คลิก UI ใน browser จริง (`/memory` ที่ 1440/390 px, DiscoveryCard, ปุ่มข้าม) — ตรวจด้วย component test + หน้า render 200
- `memory.recalled.items` = ผล FTS ของคำถามเท่านั้น โปรไฟล์ที่ฉีดทุกครั้งไม่อยู่ใน event → inspector tab Memory อาจว่างทั้งที่ prompt มีโปรไฟล์ (เช่น "ผมอยากซื้อ NVDA")
- dev DB ยังมีความจำจากการทดสอบนี้ (goal คอนโด 2 แถว, risk_capacity 2 แถว, FOMO) และ RB 5 items — ลบ/เลิกใช้ได้จากหน้า `/memory`
