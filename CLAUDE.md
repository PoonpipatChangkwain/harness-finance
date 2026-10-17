# CLAUDE.md — Finance Harness (Product)

## ภาพรวม

`Product/` คือโปรเจกต์หลัก โค้ดใหม่ทั้งหมดเขียนในโฟลเดอร์นี้
โฟลเดอร์ข้างเคียง (`../dexter`, `../TradingAgents`, `../FinRobot`, `../deepseek-harness`) เป็น **source reference แบบอ่านอย่างเดียว** ห้ามแก้ไขและห้าม import ข้ามโฟลเดอร์ ให้ศึกษาแล้ว port pattern มาเขียนใหม่ใน `Product/`

สรุปสิ่งที่ขโมยมาจากแต่ละที่ในประโยคเดียว:
- **Dexter** = ทำ agent ให้ *อยู่ได้นาน* (context, working state, executor, skills, safety)
- **TradingAgents** = ทำ agent ให้ *อยู่ในกรอบ* (graph, state, checkpoint, gate, structured routing)
- **FinRobot** = ทำ agent ให้ *กลายเป็นระบบ* (deterministic compute, domain/framework split, provenance, provider failover, workflow)

### ฟีเจอร์

| # | ฟีเจอร์ | สิ่งที่ทำ | Pillar หลัก |
|---|---------|----------|-------------|
| 1 | **Research Chat** (`/research`) | ChatBot สำหรับ deep research: หาข่าว หาข้อมูล และให้ AI expert ด้านการเงินหลายบทบาทวิเคราะห์ เลือก scenario ได้ (earnings review, macro outlook, sector scan, company valuation ฯลฯ) | D1–D5, F3, F5 |
| 2 | **Portfolio** (`/portfolio`) | ผู้ใช้กรอกพอร์ตปัจจุบัน (symbol, จำนวน, ต้นทุน, % น้ำหนัก, เงินสด) แล้ว AI วิเคราะห์และเสนอแผนปรับพอร์ต | F1, T4, T5, F3 |
| 3 | **Trading Workflow** (`/trade`) | เลือกหุ้นหรือ forex symbol หนึ่งตัว แล้วไล่ pipeline: analysts → bull/bear debate → trader → risk debate → portfolio manager → gate → approval | T1–T5, F1, F4 |

> ระบบนี้เป็นเครื่องมือวิเคราะห์และให้คำแนะนำ **ไม่ส่งคำสั่งซื้อขายจริง** คำว่า "execute" ในเอกสารนี้หมายถึงบันทึกและนำเสนอแผนที่ผ่าน approval แล้ว การต่อ broker ต้องได้คำสั่งจากผู้ใช้อย่างชัดเจนก่อน

## เอกสารแผนงาน (อ่านก่อนเขียนโค้ดทุกครั้ง)

| ไฟล์ | เนื้อหา |
|------|---------|
| [docs/README.md](docs/README.md) | ลำดับ phase 00–14 และวิธีรับงาน |
| [docs/contracts.md](docs/contracts.md) | **source of truth** ของ interface: provenance, data, tool, LLM, workflow, events, API, storage |
| [docs/data-sources.md](docs/data-sources.md) | แหล่งข้อมูลทั้งหมดที่ port จาก reference (Yahoo, SEC EDGAR, FRED, Reddit, StockTwits, X, Polymarket ฯลฯ) และลำดับ failover |
| [docs/ui-spec.md](docs/ui-spec.md) | UI แบบ deepseek-harness ที่เห็น harness ทำงานทุกขั้น |
| [docs/phases/](docs/phases/) | แผนละเอียดแต่ละ phase พร้อม Definition of Done |

ทำงานทีละ phase เท่านั้น ถ้าจะเปลี่ยน contract ให้แก้ `docs/contracts.md` ก่อน

## สถานะ phase (แหล่งความจริงของความคืบหน้า — อ่านก่อนเสมอหลัง `/clear`)

**กฎ:** ทันทีที่ phase ไหนผ่านทุกข้อใน Definition of Done ครบ (lint/typecheck/test ผ่านหมดทั้ง backend และ frontend ตามที่ phase นั้นต้องการ) ให้แก้แถวของ phase นั้นในตารางนี้เป็น `✅ เสร็จ` พร้อมวันที่และสรุปสั้นๆ **ก่อนจบงานทุกครั้ง โดยไม่ต้องรอผู้ใช้เตือน** ห้ามปล่อยให้ตารางนี้ตกยุค — นี่คือสิ่งแรกที่ session ใหม่หลัง `/clear` จะอ่านเพื่อรู้ว่าทำถึงไหนแล้วและต้องเริ่ม phase ไหนต่อ (รายละเอียดแต่ละ phase อยู่ที่ `docs/phases/phase-XX-*.md`, ลำดับ dependency อยู่ที่ `docs/README.md`)

| Phase | ชื่อ | สถานะ | หมายเหตุ |
|-------|------|--------|----------|
| 00 | Scaffold | ✅ เสร็จ | |
| 01 | Domain models + deterministic compute | ✅ เสร็จ | |
| 02 | Data layer core + Tier-A providers | ✅ เสร็จ | |
| 03 | Tier-B/C providers | ✅ เสร็จ | |
| 04 | LLM layer + structured output | ✅ เสร็จ | 2026-09-25 — provider ครบ 11 ตัว (Anthropic, Google AI Studio/Gemini, OpenAI, Grok, ThaiLLM, Ollama local, Ollama Cloud, DeepSeek, Groq, OpenRouter; Groq + Ollama Cloud เพิ่ม 2026-09-26; `claude_code` เพิ่ม P16) + auto-pick model แนะนำให้ tier quick/deep เมื่อไม่ได้เลือก provider |
| 05 | Harness runtime: events, storage, scratchpad, provenance, executor, guards | ✅ เสร็จ | 2026-09-25 — verify DoD บน live server ด้วยข้อมูล PTT.BK จริงแล้ว |
| 06 | Context management | ✅ เสร็จ | 2026-09-25 — `app/harness/context/` (config, tokens, microcompact, compact, messages, manager) coverage 99%; usage แยกตาม purpose; `context.compaction` เพิ่ม `cleared/kept` |
| 07 | Agent loop + Skills + Research workflow/API | ✅ เสร็จ | 2026-09-25 — tools 25 ตัว + memory/skill tools, 10 skills, 7 roles, workflow `research` (plan→retrieve_analyze→verify→synthesize_report), `/api/sessions`, `/api/skills`, `/api/workflows/{id}/graph`; auto-citation จาก provenance, graceful cancel, nudge เมื่อไม่เรียก tool. Live eval (ThaiLLM 8B): 4/5 เคสมี citation, DCF ขึ้นกับคุณภาพโมเดล |
| 08 | Frontend shell + Research Chat UI (MVP #1) | ✅ เสร็จ | 2026-09-26 — `/research/[[...id]]`: 3-column shell (drag handle, responsive ด้วย CSS breakpoints), reducer `stores/run-reducer.ts` (idempotent + buffer ลำดับ), SSE reconnect ด้วย `Last-Event-ID`, ToolCallTree/Process/Compaction/Answer+CitationChip/ContextMeter/StatsPills, inspector 4 tabs, Ctrl+K palette; fixture 4 แบบ (`frontend/tests/fixtures`, อัดใหม่ด้วย `RECORD_FE_FIXTURES=1`), `pnpm gen:api` จาก `backend/schemas/openapi.json`. Verify บน live server: run สด, replay หลัง refresh, Stop→cancelled, แถบ reconnect, 1440/1024/390 px; hover popover + tab Scratchpad/Provenance/Context ตรวจด้วย component test (ยังไม่ได้กดใน browser จริง) |
| 09 | Graph engine | ✅ เสร็จ | 2026-09-26 — LangGraph adapter จริง (`app/harness/graph/{checkpoint,compile,context}.py`), `validate_spec` (unreachable/router-fallback/multi-writer-ordering), `GraphRunner` (start/resume ทั้ง crash-resume และ approval-resume ผ่าน `Command(resume=…)`), `domain/policy` 8 rules + `PolicySet`, migrate `research` workflow ไปใช้ runner ใหม่ (event sequence เดิมผ่าน test ครบ), เพิ่ม API `/runs/{id}/resume`, `/runs/{id}/state`, `/approvals/{id}`, ขยาย `GET /runs/{id}` (`current_node`,`pending_approval`,`resumable`), `POST /api/dev/graph-demo` (A→B→C kill/resume demo), frontend ApprovalPanel ต่อ API จริง + ปุ่ม Resume ใน header. Backend 508 tests ผ่าน (coverage `harness/graph`+`harness/workflow` 96%), ruff/mypy --strict/import-linter ผ่านหมด; frontend typecheck/lint/test (63) ผ่านหมด, fixtures อัดใหม่ (`RECORD_FE_FIXTURES=1`) |
| 10 | Trading workflow backend | ✅ เสร็จ | 2026-09-26 — `domain/workflows/trading.py` 15 nodes + 11 routers (resolve→context→analysts→bull⇄bear→research_manager→trader→aggressive→conservative→neutral→PM→policy_gate→approval→record_decision→report), analysts รันพร้อมกันภายใน node เดียว (`asyncio.gather` ผ่าน `TradingAgent`, 1 checkpoint สำหรับทั้งขั้น) แทน parallel graph nodes; ทุกตัวเลขใน decision card มาจาก internal tool `size_trade`/`trade_snapshot` (`ToolSpec.internal`, provenance `kind=compute`), LLM เสนอแค่ stop basis; structured fail-safe → REVIEW, `MinConfidence` เป็น warn (คนอนุมัติเสมอ), policy block → trader retry 1 ครั้ง → REJECT, approval `edited` แก้ได้เฉพาะ `final_size_pct` แล้ว compute+policy ใหม่; scope checkpoint = symbol, thread = symbol/วัน/graph, ลบ checkpoint เมื่อสำเร็จ; decision log บน MemoryStore (`trade_decisions`); API `/trade/runs` (POST/GET/GET id, 409 กันรันซ้ำ), `/runs/{id}/resume` รับ `failed` ด้วย; 14 prompts `trade_*.v1.md`; schema `TradeResearchPlan`/`TraderProposal`/`PortfolioDecision` (contracts.md อัปเดตแล้ว). Backend 534 tests ผ่าน (+ live test `-m live` PTT/EURUSD ยังไม่ได้รัน), ruff/mypy/import-linter ผ่าน. **ยังไม่ทำ:** settle_decisions/reflection (optional §10.4), ทดสอบบน live server ด้วย LLM/ข้อมูลจริง |
| 11 | Trading UI (MVP #2) | ✅ เสร็จ | 2026-09-26 — หน้า `/trade/[[...runId]]`: SymbolSearch (debounce, badge, recent 10), PriceChart (lightweight-charts v5: candles/volume, SMA20/50/200 + BB, RSI/MACD pane, range, price lines entry/stop/targets, pip สำหรับ forex) ผ่าน endpoint ใหม่ `GET /market/{s}/indicators` (compute ฝั่ง backend) + `as_of`, RunSetupBar (rhf+zod, analysts ตาม asset type, backtest badge), PipelineGraph (@xyflow + dagre 2 แถว, StateDot/duration/tool badge/💾/ขอบอำพันกะพริบ/รอบ debate, edge ที่ใช้จริงไฮไลต์), DecisionCard (CitationChip ทุกตัวเลข map ตาม calculation, policy ✓✗!), TradeApprovalPanel (Edit basis: size%/stop/targets → backend compute+policy ใหม่), ResumeBanner, inspector Node·Debate·Scratchpad·Provenance·Trajectory·State, sidebar history + filter. backend: analyst sub-node `analyst.<name>` events, approval edit stop/targets, trade detail + debates/snapshot, `symbols/search.normalized`; แก้บั๊ก P09 (interrupt ถูกนับเป็น node error), usage สะสมข้าม approval pause, report ใช้ policy ที่ตรวจใหม่หลัง edit. reducer มี `nodes`/`path`. ทดสอบจริง (groq + yfinance): PTT→no_trade, AAPL→approval→edit→approved, kill backend ระหว่าง risk debate→ResumeBanner→resume ไม่รัน analysts ซ้ำ. backend 542 passed; frontend 90 tests |
| 12 | Portfolio backend + advisor | ✅ เสร็จ | 2026-09-26 — ตาราง `portfolios/positions/portfolio_revisions/plans/portfolio_snapshots` (migration 0003, `harness/storage/portfolio_repo.py`, ทุกการแก้ = revision + provenance `user_input`); `domain/portfolio/` (`analytics.py` quote+FX+1y history → values/weights/P&L/day P&L/HHI/exposure/vol/MDD/beta/VaR/CVaR/corr/risk contribution ทุกตัวเลข `Provenanced`, declared-% mode, ตัวไหนไม่มีราคา → warning ไม่เดา weight; `entry.py` 3 โหมด qty / มูลค่า+%กำไร / % + CSV; `summary.py` มุมมองรวมหลายพอร์ต; `apply.py`); compute ใหม่ `pnl_summary/units_from_value/post_trade/trim_buys_to_cash/risk_contribution`; policy ใหม่ `MinCashWeight/NoSellList/MaxTurnover`; workflow `portfolio_advisor` (load → analytics → diagnose ∥ 2 roles → propose (structured `RebalanceProposal`, normalize+warning) → `rebalance_plan` (lot 100) → policy (retry 1) → approval (edit target weights) → save_plan → report; ไม่แก้ positions จนกด apply); API `/portfolio*` + `/portfolios*` (CRUD, summary, import, advise, plans/apply, revisions, snapshots, reports); trading อ่านพอร์ตจริงแบบ 3 สถานะ (equity/cash แปลงเป็นสกุลเงินของ symbol, policy ใช้ weight รวม); research `portfolio_ids` toggle + session `kind=portfolio` + tool `get_portfolio_analytics`; skills `portfolio-risk-review`, `portfolio-weakness`; weekly job ศุกร์ 03:00 BKK + catch-up (`PORTFOLIO_WEEKLY_ENABLED`). Backend 606 tests ผ่าน, ruff/mypy/import-linter ผ่าน; ตรวจ DoD บน live server แล้ว. **ยังไม่ทำ:** frontend (P13), `pnpm gen:api` ยังไม่ได้รัน |
| 13 | Portfolio UI + Settings (MVP #3) | ✅ เสร็จ | 2026-09-26 — หน้า `/portfolio`: chips ทั้งหมด/ต่อพอร์ต/+พอร์ตใหม่ (สร้าง/แก้/ลบ), ภาพรวมแบบ Dime! (`PortfolioOverview`: แยกสินทรัพย์/แยกพอร์ต, ตลาด ทั้งหมด·ไทย·สหรัฐฯ, USD ≈THB + FX, donut ตามพอร์ต, sort มูลค่า/กำไร), หน้าพอร์ต: `SummaryTiles`, `HoldingsTable` (TanStack v9, แก้ inline, แถว draft แบบ qty หรือ มูลค่า+% กำไร, 422 ที่แถว, โหมด % อย่างเดียว + ผลรวมสด, sort/filter/คอลัมน์), `ImportCsvDialog` (papaparse → server preview → commit), `AllocationChart` (Recharts donut 4 มุมมอง), `ExposureBars` (limit จาก Settings), `RiskPanel` + `CorrelationHeatmap` + contribution, `ProposalTable` (approve/edit target%/reject → backend คำนวณ+policy ใหม่), `PlansHistory` (apply ผ่าน dialog diff, force เมื่อพอร์ตเปลี่ยน, revisions), `WeeklyReports` (+รันตอนนี้); ทุกตัวเลขเป็น `ProvValue` (hover = provenance, คลิก = lineage ใน inspector); inspector `AI Advisor` (session `kind=portfolio`, quick actions 3 ปุ่ม, advisor run → scroll+highlight proposal), Research Composer toggle `ใช้ข้อมูลพอร์ต` + chip บน turn. `/settings`: LLM deep/quick + failover (drag), data order ต่อ capability (drag/↑↓), limits (+ค่า Dexter), policy, appearance (theme, ขนาดตัวอักษร); reset ต่อ section. backend: ตาราง `settings` (migration 0004), `app/harness/config/overrides.py` (apply ลง Settings + `DataRouter.set_order_overrides` + policy config ของ trading/advisor, โหลดตอน start), `GET/PUT /api/settings` (ไม่มี key), `GET /portfolios/{id}/advisor-runs`, `pnpm gen:api` แล้ว. Backend 614 passed (+ fixture `advisor-run-edit.jsonl`), ruff/mypy/import-linter ผ่าน; frontend 112 tests, lint/typecheck ผ่าน; ตรวจ DoD บน live server (ดู phase-13) |
| 14 | Hardening: evals, E2E, budget, docs | ⬜ ยังไม่เริ่ม | **phase ถัดไป** — Playwright E2E ยังไม่มี (P13 ตรวจ UI ด้วย component test + screenshot) |
| 15 | Backtest + portfolio simulator | ✅ เสร็จ | **15.3–15.4 (2026-10-01):** workflow `backtest` (cells = trading run ลูกหลัง `PointInTimeRouter`, auto-approval, cache ข้าม backtest, budget, resume), migration 0005, `/api/backtests*` (estimate/start/detail/simulate/delete), events `backtest.*`, หน้า `/backtest` (setup + เงินต้น, CellGrid, equity vs buy&hold vs SMA, what-if, trades); past-dated `/trade` run ใช้ point-in-time เสมอ. Backend 724 passed (+fail เดิม 1), frontend 138 tests, lint/typecheck ผ่าน; ทดสอบจริงบน live server (PTT 4 วัน, Ollama Cloud) ผ่าน. รายละเอียดเดิมของ 15.1–15.2: | 2026-10-01 — ดู `docs/phases/phase-15-backtest.md`; แทน `evals/trading_backtest.py` ใน 14.1. **15.1:** yfinance ดึงช่วงที่จบที่ `as_of`, แก้แท่งรายวันตลาดเอเชียลงวันผิด 1 วัน (look-ahead + กราฟ), lessons กรองตาม `analysis_date`, `PointInTimeRouter` + `registry.use_router()`. **15.2:** `domain/compute/backtest/` (simulate, performance, buy_and_hold, sma_cross) 27 tests cov 98%. Backend 717 passed (fail 1 ตัวเดิม `test_unconfigured_providers_no_network` FRED — มีก่อนแล้ว), ruff/mypy/import-linter ผ่าน. **ถัดไป 15.3:** แก้ contracts.md ก่อน แล้วทำ mode=backtest + workflow + API |
| 16 | Portfolio entry แบบโบรกเกอร์ + LLM provider `claude_code` (subscription) | ✅ เสร็จ | 2026-10-14 — **LLM:** provider `claude_code` (`harness/llm/claude_code.py`) = Claude ผ่าน subscription ของผู้ใช้ โดย spawn Claude Code CLI headless (`claude -p --output-format json`, prompt ทาง stdin, system prompt ทางไฟล์, env ตัด API key/`CLAUDECODE` ใส่ `CLAUDE_CODE_OAUTH_TOKEN` จาก `ANTHROPIC_API_SUB_KEY`); tool calling จำลองผ่าน `--json-schema` envelope, structured ผ่าน `--json-schema`; configured = พบ CLI + (token หรือ login ในเครื่อง); Settings catalog มี `note`/`configured_reason`; `Settings` อ่าน `../.env` (Product/.env) เป็น fallback ของ `backend/.env`. **ใช้ส่วนตัวเท่านั้น** (เงื่อนไข Anthropic) ยังไม่มี streaming. **Portfolio:** `PositionInput` เพิ่ม `mode` (7 โหมด: qty_cost/qty_pnl_pct/qty_pnl_amount/value_pnl_pct/value_pnl_amount/cost_total/weight), `pnl_amount`, `cost_total`; compute ใหม่ 4 ฟังก์ชัน + `position_value`; `POST …/positions/preview` (ไม่บันทึก, provenance ต่อฟิลด์), `POST /portfolios/parse-holdings` (วางข้อความจากแอปโบรก → quick LLM structured, prompt `portfolio_parse_holdings.v1`); frontend `AddPositionDialog` (6 โหมด + ปุ่มกำไร/ขาดทุน + preview สด), `PasteHoldingsDialog`, เมนูโหมดใน `HoldingsTable`. Backend 890 passed (fail เดิม 1 ตัว FRED), ruff/mypy/import-linter ผ่าน; frontend 166 tests, lint/typecheck ผ่าน, `pnpm gen:api` แล้ว. ทดสอบสด: preview 6 โหมด PTT/KBANK/AAPL/NVDA/MSFT/GOOGL ตัวเลขตรง, parse-holdings 2 สไตล์ (gemma4 + claude_code haiku native), research run claude_code haiku (2 tool calls, citation 2 ตัว), trade run AAPL claude_code haiku ครบ pipeline → no_trade (structured ผ่านทุก node). **ยังไม่ได้:** คลิก UI ใน browser จริง (ตรวจด้วย component test + หน้า /portfolio /settings render 200), streaming ของ claude_code, อัปโหลดภาพหน้าจอพอร์ต. **แก้ 2026-10-14:** CLI ใหม่ (≥2.1.2xx) ไม่มี `cli.js` → เรียก `bin/claude.exe` ตรง; uvicorn `--reload` บน Windows ใช้ SelectorEventLoop ที่ spawn subprocess ไม่ได้ → fallback `Popen` ใน thread; `LLMUnavailable` แนบ error detail |
| 17 | AI Portfolio Advisor memory: long-term user memory (Mem0 pattern, supersede ไม่ลบ) + ReasoningBank + 5 Whys ใน chat + หน้า `/memory` | ✅ เสร็จ (branch `FinTechathon`) | 2026-10-17 — แผน `docs/phases/phase-17-advisor-memory.md`, วิจัย `docs/research/advisor-styles-memory.md`, ผลทดสอบสด `docs/phases/phase-17-verification.md`. **Backend:** `domain/memory/` (schemas, ports, profile ≤600 tokens, scrub, discovery `should_ask`, tags, 7 prompts v1) + `harness/memory/` (store, engine extract→reconcile ADD/UPDATE/SUPERSEDE/RETIRE/NOOP พร้อม guard แบบโค้ด, bank distill→scrub→privacy judge→retrieve/settle, tool `user_memory_search`); migration 0006 `user_memory`/`reasoning_bank`/`reasoning_bank_uses` + FTS5 trigram (ค้นไทยได้); API `/memory*` 8 + `/reasoning-bank*` 4 (header `X-User-Id`, default `usr_default`); research workflow **v2** `plan→recall_memory→assess_discovery→{ask_only→write_question \| research[_then_ask]→…→synthesize_report}→learn` (`skip_discovery`, session meta `discovery`, `AgentRunRequest.context_blocks`), advisor v2 (`recall_memory` + `learn` หลัง approve/reject, goal จาก memory), trade outcome → RB; events `memory.recalled/discovery.assessed/memory.updated/reasoning.distilled` + `answer.final.discovery`; contracts §13. **Frontend:** `/memory` (profile, list ตามหมวด, ยืนยัน/แก้/เลิกใช้/ลืม/ประวัติ, tab ReasoningBank), MemoryChip, DiscoveryCard n/5 + ปุ่มข้าม, inspector tab Memory, AdvisorPanel profile line; fixture `research-discovery.jsonl`. Backend 1011 passed (fail เดิม FRED 1), frontend 207 tests, lint/typecheck/import-linter ผ่าน; live (groq gpt-oss-120b + ollama_cloud gemma4): supersede ✓, NVDA 5 Whys → identified ใน 3 turn ✓, skip ✓, advisor approve → RB 2 items ไม่มี ticker/จำนวน และ use_count/success_rate อัปเดต ✓, forget cascade ✓; overhead 2–4 quick calls/turn. **ยังไม่ทำ:** คลิก UI ใน browser จริง, embedding/hybrid retrieval, multi-user (k-anonymity gate), `memory.recalled` ยังไม่รวม profile items (inspector tab ว่างได้แม้ prompt มี profile) |

## Tech stack (ยืนยันแล้ว)

- **Backend:** Python 3.12 (`uv`), FastAPI + `sse-starlette`, LangGraph + `langgraph-checkpoint-sqlite`, Pydantic v2, SQLAlchemy 2 + Alembic (SQLite), httpx, pandas; LangChain ใช้ได้เฉพาะใน `app/harness/llm` และ `app/harness/graph`
- **Frontend:** Next.js 15 (App Router) + React 19 + TypeScript strict (`pnpm`), Tailwind v4 + shadcn/ui, Zustand, `@microsoft/fetch-event-source`, `lightweight-charts`, Recharts, `@xyflow/react`, `@tanstack/react-table`; ฟอนต์ IBM Plex Sans Thai + JetBrains Mono
- **Streaming:** SSE แบบ replay ได้ (event เก็บใน DB, ใช้ `Last-Event-ID`) และ event types ตาม `docs/contracts.md` §10
- **Storage:** SQLite (`DATA_DIR/app.db`), scratchpad JSONL, checkpoint DB แยกตาม symbol
- **Test:** `pytest` (+ `respx`, FakeLLM), `vitest`, Playwright (E2E)

---

## สถาปัตยกรรม: Harness ≠ Domain (F2 คือกฎข้อแรก)

```
┌──────────────────────── HARNESS (app/harness) ────────────────────────┐
│ orchestration · state · checkpoint · tool executor · gates · retry    │
│ context/compaction · scratchpad · LLM providers · provenance store    │
│ ไม่รู้จักคำว่า "หุ้น" — ใช้กับ domain อื่นได้                          │
└───────────────────────────────┬───────────────────────────────────────┘
                                │ interfaces: Tool, Skill, Workflow, DataProvider
                                ▼
┌──────────────────────── DOMAIN (app/domain) ──────────────────────────┐
│ agents (roles/prompts) · skills · tools · compute (deterministic)     │
│ data providers · models · workflow definitions · policy rules         │
│ ห้าม import langgraph / langchain / fastapi                           │
└───────────────────────────────────────────────────────────────────────┘
```

**กฎทดสอบ:** ถ้าเปลี่ยน LangGraph เป็น PydanticAI ต้องแก้แค่ `app/harness/graph/` (adapter) เท่านั้น domain ต้องไม่ถูก rewrite
ให้มี test ที่ import ทุกโมดูลใน `app/domain/` โดยไม่มี langgraph/langchain/fastapi ติดตั้งอยู่ (หรือใช้ `import-linter` contract)

### โครงสร้างโฟลเดอร์

```
Product/
  backend/app/
    api/                      # FastAPI routes + SSE: research, portfolio, trade, runs (resume/approve)
    harness/
      runtime/                # agent loop, limits, loop detection, retry, timeout (D5)
      context/                # microcompact, full compact, token budget (D1)
      state/                  # conversation / scratchpad (working) / memory (persistent) (D2)
      executor/               # validate → permission → execute → normalize (D3)
      skills/                 # SKILL.md loader + registry (D4)
      graph/                  # LangGraph adapter: compile workflow defs, reducers, checkpointer (T1–T3)
      gates/                  # policy runner + human approval via interrupt (T4)
      structured/             # structured call + fallback + coercion (T5)
      llm/                    # provider interface, factory, failover (F4)
      provenance/             # record + store + number-check (F3)
      workflow/               # Workflow base class + registry (F5)
      memory/                 # P17: user memory store (FTS5) + MemoryEngine (recall/learn) + ReasoningBank + tool user_memory_search
    domain/
      models/                 # Symbol, Position, Portfolio, Decision schemas, ProvenancedValue
      compute/                # portfolio math, indicators, risk, valuation — pure functions (F1)
      data/                   # DataProvider interfaces + providers/ (yfinance, alpha_vantage, fred, news...) (F4)
      tools/                  # primitives ที่ห่อ compute/ และ data/
      skills/                 # research/ analysis/ data_processing/ reporting/ — SKILL.md (D4)
      agents/                 # role prompts (versioned) + output schemas
      policy/                 # deterministic risk rules สำหรับ gate (T4)
      workflows/              # research.py, portfolio.py, trading.py (F5)
      memory/                 # P17: schemas, ports, profile, scrub (PII), discovery (5 Whys), prompts/*.v1.md
    tests/
  frontend/app/{research,portfolio,trade}/
```

---

## Pillars — สิ่งที่ต้อง port และวิธีทำ

อ่านไฟล์ reference ของ pillar นั้นก่อน implement ทุกครั้ง และระบุใน commit ว่าอิง pattern จากไฟล์ไหน

### Dexter — ทำ agent ให้อยู่ได้นาน

#### D1 · Context management ⭐⭐⭐⭐⭐ (สำคัญที่สุดของ Dexter)
Reference: `../dexter/src/agent/microcompact.ts`, `compact.ts`, `agent.ts` (manageContextThreshold), `../dexter/src/utils/tokens.ts`, `../deepseek-harness/packages/compaction/`

ปัญหา: tool result สะสมทุก iteration จนกิน context 200k → 400k → 800k 💥 ต้องมี 2 ชั้น

| | Micro-compaction | Full compaction |
|---|---|---|
| เมื่อไหร่ | **ทุก turn** ก่อนเรียก LLM เมื่อ tool result ที่ compact ได้มีเกิน 8 ชิ้น หรือรวมเกิน 80k tokens | เมื่อ context เกิน threshold ที่คิดจาก context window ของ model (Dexter ใช้ 100k) |
| ทำอะไร | แทนเนื้อหา tool result เก่าด้วย `[Old tool result content cleared]` และเก็บ 4 ชิ้นล่าสุดไว้ | ให้ fast LLM สรุปทั้ง session เป็นโครงสร้างตายตัว แล้วแทนที่ message เดิม |
| ใช้ LLM | ไม่ใช้ | ใช้ (text only ห้ามเรียก tool) |

- **Token budget:** ใช้ `input_tokens` จริงจาก API response ล่าสุดเป็นหลัก ใช้การประมาณจากจำนวนตัวอักษรเป็นแค่ fallback
- **ต้องเก็บ:** คำถามและเจตนาของผู้ใช้, symbol/บริษัท, **ตัวเลขทุกตัว** พร้อมแหล่งที่มา, error และ retry, ข้อสรุปที่ได้แล้ว, ข้อมูลที่ยังขาด, สิ่งที่กำลังทำ, next step (ใช้ 9 หัวข้อตาม `BASE_COMPACT_PROMPT` ใน compact.ts)
- **ทิ้งได้:** raw payload ของ tool result เพราะยังอยู่ใน scratchpad, ผลลัพธ์ที่ซ้ำ, และ reasoning ระหว่างทางที่ได้ข้อสรุปแล้ว
- **Failure:** ถ้ามี tool result น้อยกว่า 3 ชิ้นให้ใช้การ clear แทน ถ้า compaction ล้มเหลวให้ fallback เป็นการ clear เก่าสุด (เก็บไว้ 5 ชิ้น) และหยุดพยายาม compact หลังล้มเหลวติดกัน 3 ครั้ง
- ทุกการ compact ต้องส่ง event `compaction` และบันทึกลง scratchpad
- **ข้อยกเว้น provider `claude_code`:** count trigger ปิดเป็นค่าเริ่มต้น (`CLAUDE_CODE_MC_COUNT_TRIGGER`) ใช้ token trigger อย่างเดียว และ timeout ต่อ call ใช้ `CLAUDE_CODE_TIMEOUT_S` (300s) แทน `LLM_TIMEOUT_S`

#### D2 · Scratchpad / Working state ⭐⭐⭐⭐⭐
Reference: `../dexter/src/agent/scratchpad.ts`, `run-context.ts`, `../dexter/src/memory/`

**state 3 ชนิดนี้ห้ามปนกัน:**

| ชนิด | คืออะไร | อายุ | Compact ได้? | ที่อยู่ |
|------|---------|------|--------------|--------|
| Conversation state | message ที่ส่งให้ LLM และแสดงใน chat | 1 session | ได้ (D1) | `harness/state/conversation.py` |
| Working state | scratchpad ของ run: task, tool call, tool result, reasoning ระหว่างทาง, next action | 1 run | **ห้าม** เป็นแบบ append-only | `harness/state/scratchpad.py` (JSONL ต่อ run) + graph state ของ workflow |
| Persistent memory | สิ่งที่ข้าม session: พอร์ตผู้ใช้, preference, การตัดสินใจในอดีต + reflection | ถาวร | ไม่เกี่ยว | `harness/state/memory.py` (SQLite) |

- Scratchpad คือ single source of truth ของ run: final answer และ provenance ต้องสร้างจาก scratchpad ไม่ใช่จาก conversation ที่ถูก compact แล้ว
- Final answer สร้างด้วย LLM call แยกที่ **ไม่ bind tool**
- เขียนลง persistent memory ได้เฉพาะจาก step ที่ตั้งใจเขียนเท่านั้น (เช่น "save decision") ห้ามเขียนอัตโนมัติจาก conversation

#### D3 · Tool executor ⭐⭐⭐⭐ — runtime boundary
Reference: `../dexter/src/agent/tool-executor.ts`, `../dexter/src/tools/registry.ts`, `../dexter/src/permissions/`

**LLM ขอได้ แต่ Harness เป็นคน execute** ทุก tool call ต้องผ่าน pipeline นี้:

```
LLM tool call → validate args (Pydantic) → permission (allow/ask/deny) → execute (timeout, cancel)
             → normalize result → append scratchpad + provenance → กลับไปหา LLM
```

- Result ที่ normalize แล้วมีรูปแบบเดียวกันทุก tool: `{ok, data, error, truncated, provenance_ids}` และ error ต้องคืนเป็น result ให้ LLM เห็น ห้ามโยน exception ทำให้ loop พัง
- tool ที่ประกาศ `read_only=True` จัดเป็น batch รันพร้อมกันได้ (สูงสุด 10) ส่วน tool ที่มี side effect รันทีละตัวและต้องผ่าน permission
- ตัด output ที่ใหญ่เกินกำหนดแล้วตั้ง `truncated=True` ส่วนเนื้อหาเต็มเก็บไว้ใน scratchpad

#### D4 · Skills ⭐⭐⭐⭐ — Tool = primitive, Skill = workflow
Reference: `../dexter/src/skills/` (loader, registry, `dcf/SKILL.md`, `write-memo/`), `../dexter/src/tools/skill.ts`, `../deepseek-harness/packages/skill/`

- **Tool** = ทำอย่างเดียว ไม่เรียก LLM เช่น `get_price`, `get_financials`, `search_news`, `compute_weights`
- **Skill** = ขั้นตอนที่เขียนเป็น `SKILL.md` (frontmatter `name`, `description`) ที่ agent ทำตามได้ทันทีโดยไม่ต้องคิด workflow ใหม่ตั้งแต่ต้น เช่น `company_valuation`: search → financials → compute (tool) → report
- แบ่งหมวดตามนี้: `domain/skills/{research,analysis,data_processing,reporting}/<skill>/SKILL.md`
- Scenario ใน Research Chat คือ skill ที่ผู้ใช้เลือกไว้ล่วงหน้า registry ค้นหาเจออัตโนมัติ และแต่ละ skill รันได้ครั้งเดียวต่อ query
- ถ้า skill ต้องคำนวณ ให้เรียก tool ของ `compute/` ห้ามให้ LLM คำนวณตามคำอธิบายใน SKILL.md (F1)

#### D5 · Long-running agent safety ⭐⭐⭐⭐
Reference: `../dexter/src/agent/agent.ts` (maxIterations), `scratchpad.ts` (ToolLimitConfig), `../dexter/src/model/llm.ts` (withRetry), `../deepseek-harness/packages/guard/` (timeout-policy, repeat-tool-reminder), `../TradingAgents/tradingagents/agents/post_screen.py` (partial failure)

| กลไก | ค่าเริ่มต้น / กฎ |
|------|-----------------|
| Max iterations | 10 ต่อ research query; graph ใช้ `recursion_limit` |
| Loop detection | tool เดียวกันเรียกได้ไม่เกิน 3 ครั้งต่อ query และถ้า args คล้ายครั้งก่อน (similarity ≥ 0.7) ให้ block แล้วบอก LLM |
| Timeout | ทุก tool และ LLM call มี timeout ของตัวเอง และทุก run มี wall-clock budget รวม รองรับ cancel ผ่าน signal |
| Budget | จำกัด token/cost ต่อ run และหยุดเมื่อถึงเพดาน 💸 |
| Retry | exponential backoff (`500ms × 2^n`, ไม่เกิน 3 ครั้ง) **เฉพาะ** error ชั่วคราว (429, 5xx, timeout, connection) ส่วน error ที่ retry ไม่ได้ (auth, validation) ให้ fail ทันที |
| Tool failure | คืนเป็น error result ให้ LLM ตัดสินใจต่อ (D3) |
| Partial failure | ทำงานต่อด้วยข้อมูลที่มี และระบุใน output ว่าส่วนไหนขาด/ไม่พร้อม ห้ามเงียบ |
| Graceful stop | เมื่อชน limit ให้สร้างคำตอบจาก scratchpad พร้อมบอกว่าหยุดเพราะอะไร ห้าม crash |

### TradingAgents — ทำ agent ให้อยู่ในกรอบ

#### T1 · Explicit graph orchestration ⭐⭐⭐⭐⭐
Reference: `../TradingAgents/tradingagents/graph/setup.py`, `trading_graph.py`, `analyst_execution.py`

Graph เป็นคนกำหนดว่าใครทำอะไร เมื่อไหร่ และส่ง state ให้ใคร **ไม่ใช่ LLM คิดเองว่าจะทำอะไรต่อ**
- Workflow ใน `domain/workflows/` ประกาศ nodes/edges/routers เป็นข้อมูลธรรมดา แล้ว `harness/graph/` compile เป็น LangGraph `StateGraph` (ทำให้ F2 เป็นจริง)
- analyst ที่ใช้ tool จะวนระหว่าง agent node กับ tool node และมี clear node ปิดท้ายตามแบบ `_tools_or_clear`
- LLM เลือก tool ได้เฉพาะภายใน node เท่านั้น ห้ามเลือก node ถัดไปเอง

#### T2 · State machine / Shared state ⭐⭐⭐⭐⭐
Reference: `../TradingAgents/tradingagents/agents/state.py`, `graph/conditional_logic.py`

ต้องเข้าใจและใช้ให้ครบ: **State · Reducer · Node · Edge · Conditional Edge · Checkpoint**
- State เป็น TypedDict/Pydantic ที่ประกาศทุก field ชัดเจน แบ่งเป็นส่วน `task`, `observations`, `analysis`, `decision`, `risk`
- Node เป็นฟังก์ชันที่รับ state แล้วคืน **partial update** เท่านั้น ห้าม mutate state
- ทุก field มีเจ้าของ (node ที่เขียนได้) คนเดียว ถ้าหลาย node เขียน field เดียวกัน (เช่น messages, debate history) ต้องประกาศ reducer
- Conditional router ต้องคืนค่าที่อยู่ใน path map **ทุกกรณี** รวมถึงกรณี fall-through (ดู `DEBATE_PATH_MAP`) เพื่อไม่ให้ graph crash กลาง run
- จำนวนรอบ debate กำหนดผ่าน config

#### T3 · Checkpoint / Resume ⭐⭐⭐⭐⭐
Reference: `../TradingAgents/tradingagents/graph/checkpointer.py`, `trading_graph.py` (checkpoint_scope), `../deepseek-harness/packages/session/`

- checkpoint หลังทุก node: ถ้า A ✓ → B ✓ → C 💥 ให้ resume ที่ C ไม่ต้องเริ่ม A ใหม่
- `thread_id = hash(symbol, date, graph_signature)` โดย signature รวมทุกอย่างที่เปลี่ยนรูปร่าง graph (analyst ที่เลือก, จำนวนรอบ, มี/ไม่มี portfolio) เพื่อไม่ให้ resume ข้าม graph ที่ต่างกัน
- SQLite แยก DB ต่อ symbol และต้อง sanitize symbol ก่อนใช้เป็น path
- API มี `POST /runs/{id}/resume` และลบ checkpoint เมื่อ run จบสำเร็จ
- การ resume ต้องไม่เรียก tool ที่มี side effect ซ้ำ (idempotency key ต่อ node)

#### T4 · Approval / Gate architecture ⭐⭐⭐⭐⭐
Reference: `../TradingAgents/tradingagents/agents/managers/portfolio_manager.py`, `agents/risk_mgmt/`, `../dexter/src/permissions/`, LangGraph `interrupt()`

```
Agent proposes → Policy/Validator (deterministic) → Human หรือ reviewer agent → Execute (บันทึก/นำเสนอแผน)
```

- **Policy** เป็นโค้ดใน `domain/policy/` ไม่ใช่ LLM เช่น น้ำหนักต่อตัวไม่เกิน X%, ต่อ sector ไม่เกิน Y%, ต้องมี stop-loss, symbol ต้องถูกต้อง, ห้ามใช้เงินเกินเงินสดที่มี ถ้าผิด policy ให้ reject หรือส่งกลับไปแก้ ห้ามปล่อยผ่าน
- **Human approval** ใช้ `interrupt()` ส่ง event `approval_required` (มีข้อเสนอ, ผล policy, provenance) ไปที่ UI แล้ว resume ด้วยคำตอบ approve / reject / edit
- LLM ไม่มีสิทธิ์ execute อะไรที่มีผลจริงโดยตรง ต้องผ่าน gate เสมอ

#### T5 · Structured output + Conditional routing ⭐⭐⭐⭐
Reference: `../TradingAgents/tradingagents/agents/structured.py`, `schemas.py`, `rating.py`

ห้ามให้ agent ที่ตัดสินใจคืนข้อความแบบ "ผมคิดว่าน่าจะ..." แล้วให้ graph เดาเอง ต้องคืน schema เช่น:

```json
{ "decision": "APPROVE | REVIEW | REJECT", "confidence": 0.81, "reason": "...", "next_action": "risk_check" }
```

- Router อ่านเฉพาะ field ที่เป็น enum/ตัวเลขซึ่ง parse แล้วเท่านั้น: `REJECT → END`, `REVIEW → risk/human`, `APPROVE → gate → execute`
- **Fail-safe:** parse ไม่ได้, confidence ต่ำกว่า threshold หรือ field หาย ให้ถือเป็น `REVIEW` ห้าม default เป็น `APPROVE` เด็ดขาด
- ใช้ `with_structured_output` และถ้าล้มเหลวให้ fallback เป็น free-text แล้ว parse ตามแบบ `structured.py`
- coerce ตัวเลขที่ LLM เขียนมั่ว ("N/A" → None, "$1,234" → 1234, "15%" ในช่องราคา → None) ตามแบบ `_coerce_optional_float`
- Render schema กลับเป็น markdown เพื่อแสดงผล แต่ตัวที่เก็บจริงคือ schema

### FinRobot — ทำ agent ให้กลายเป็นระบบ

> ⚠️ **ข้อจำกัดของ reference:** จุดขายหลักของ FinRobot (30 compute operators, provenance, 7 providers + failover, 7 pipelines) อยู่ใน **V2 Desktop (PydanticAI) ซึ่งยังไม่ open-source** ส่วนที่มีในเครื่องคือ V0 (`finrobot/`, AutoGen) และ V1 (`finrobot_equity/`) ดังนั้น F3–F5 ให้ศึกษาแนวคิดจาก `../FinRobot/README.md` (บรรทัด ~79–134) แล้วออกแบบเอง โดยใช้ TradingAgents/Dexter เป็นตัวอย่างโค้ดแทน

#### F1 · LLM ≠ Deterministic computation ⭐⭐⭐⭐⭐
Reference: `../FinRobot/finrobot_equity/core/src/modules/valuation_engine.py`, `sensitivity_analyzer.py`, `../FinRobot/finrobot/functional/quantitative.py`

**LLM ทำ reasoning ส่วน code ทำสิ่งที่ต้องแม่นยำ**
```
LLM → กำหนด assumptions (schema ที่มีขอบเขต) → compute/ (Python) → ตัวเลข + provenance → LLM อธิบาย
```
- ของที่ต้องคำนวณใน `domain/compute/` เท่านั้น: % น้ำหนัก, P&L, return, volatility, drawdown, correlation, concentration, indicator (SMA/EMA/RSI/MACD/ATR), position size, stop distance, DCF/WACC/comps, pip value ของ forex
- assumptions ที่ LLM เสนอ (เช่น growth, WACC) ต้อง validate ขอบเขตก่อนคำนวณ และบันทึกลง provenance ว่า LLM เป็นคนเสนอ
- ฟังก์ชันใน compute เป็น pure function ใช้ `Decimal` สำหรับเงิน และต้องมี unit test

#### F2 · Domain layer ≠ Agent framework ⭐⭐⭐⭐⭐
Reference: `../FinRobot/README.md` (V0 AutoGen → V1 OpenAI Agents SDK → V2 PydanticAI → V3 DeepSeek-Harness), `../FinRobot/finrobot/toolkits.py` (register function กับ agent ใดก็ได้)

ดูหัวข้อ "สถาปัตยกรรม" ด้านบน tool ใน domain เป็น Python function ธรรมดาที่มี type hints และ docstring แล้ว harness แปลงเป็น tool ของ framework ภายหลัง

#### F3 · Provenance ⭐⭐⭐⭐⭐
Reference (แนวคิด): `../FinRobot/README.md` · (โค้ดใกล้เคียง): `../TradingAgents/tradingagents/decision_log.py`, `../dexter/src/agent/scratchpad.ts`

ทุกตัวเลขหรือข้อเท็จจริงสำคัญต้องตอบได้ว่ามาจากไหน คำนวณอย่างไร ใช้ข้อมูลอะไร และมาจากแหล่งใด
```json
{ "id": "prov_…", "value": 123.4, "unit": "USD", "source": "yfinance", "tool": "get_price",
  "timestamp": "2026-09-24T10:00:00Z", "as_of": "2026-09-23", "calculation": "compute.weights@v1",
  "inputs": ["prov_…"], "agent": "portfolio_advisor", "run_id": "…" }
```
- `domain/data` และ `domain/compute` คืน `ProvenancedValue` เสมอ ส่วน harness เก็บ record ลง provenance store
- ทุก run บันทึก model, prompt version, tool calls และ output สำหรับ debug, audit และ reproduce
- **Hallucination check:** ตัวเลขใน final answer ต้องจับคู่กับ provenance record ได้ ตัวที่จับคู่ไม่ได้ให้ flag ใน UI
- UI แสดงแหล่งที่มาของตัวเลขได้ (hover/citation)

#### F4 · Provider abstraction + failover ⭐⭐⭐⭐
Reference: `../TradingAgents/tradingagents/dataflows/router.py`, `errors.py`, `vendors/`, `../TradingAgents/tradingagents/llm_clients/factory.py`, `base_client.py`, `../dexter/src/model/llm.ts`

**Agent ต้องไม่รู้ว่าข้อมูลมาจาก vendor ไหน**
```
Agent/Tool → DataProvider interface (ต่อ capability) → Provider A ✗ → B ✗ → C
```
- แยก capability: `prices`, `fundamentals`, `news`, `macro`, `forex`, `filings` และแต่ละ capability มีลำดับ provider ใน config
- error มีประเภทชัดเจน (`RateLimited`, `NoData`, `NotConfigured`, `Transient`) ซึ่งเป็นตัวกำหนดว่าจะ failover หรือ fail
- มี cache ต่อ `(provider, capability, params, as_of)` และ provenance บันทึกว่า provider ไหนตอบจริง
- LLM ใช้หลักเดียวกัน: `BaseLLMClient`, factory, แยก `deep_think` กับ `quick_think` model และ failover ไป provider สำรอง
- Unit test ใช้ recorded fixtures ห้ามเรียก API จริง

#### F5 · Workflow เป็น first-class object ⭐⭐⭐⭐⭐
Reference: `../FinRobot/finrobot/agents/workflow.py`, `agent_library.py`, `../deepseek-harness/packages/workflow/`

ห้ามเขียนทุกอย่างเป็น `Agent.run()` แต่ละ workflow ต้องประกาศ `id`, `input_schema`, `steps`, `validation`, `output_schema` และลงทะเบียนใน registry รูปแบบมาตรฐานคือ:
```
retrieve → analyze → verify → compute → synthesize → report
```

| Workflow | Steps |
|----------|-------|
| `research` | resolve scenario/skill → retrieve (agent loop D1–D5) → analyze (expert roles) → verify (provenance/number check) → compute → synthesize → report |
| `portfolio` | validate input → fetch prices → compute (weights, P&L, concentration, risk) → analyze (advisor, structured T5) → policy gate → human approval → report |
| `trading` | resolve symbol (หุ้น/forex) → analysts (market, news, fundamentals*, sentiment) → bull/bear debate → research manager → trader → risk debate → portfolio manager → policy gate → human approval → report |

\* forex ไม่มี fundamentals ของบริษัท ให้ใช้ macro/rates แทน
- role ของ agent กำหนดผ่าน library/config (ตามแบบ `agent_library.py`) ห้าม hardcode
- เพิ่ม workflow ใหม่ต้องทำได้โดยไม่แก้โค้ดใน `harness/`

### deepseek-harness — reference ด้านสถาปัตยกรรม
ใช้ดูแนวคิด plugin และการแบ่ง package (`../deepseek-harness/docs/architecture.md`, `AGENTS.md`) เท่านั้น ห้ามลอก Cordis runtime เข้ามา

---

## กฎการเขียนโค้ด

- Python: type hints ครบ ใช้ Pydantic ที่ boundary ทุกจุด ใช้ `ruff` และ `mypy --strict` ใน `domain/` กับ `harness/`
- TypeScript: strict mode และห้ามใช้ `any`
- แยก 3 สถานะของพอร์ตให้ชัด: มี position / พอร์ตว่าง / ไม่ได้ให้ข้อมูล (ดู `../TradingAgents/tradingagents/portfolio.py`) และห้ามตีความ "ไม่ได้ให้ข้อมูล" ว่าเป็น "พอร์ตว่าง"
- Symbol ต้อง normalize และ validate ก่อนใช้ (หุ้นไทย `.BK`, US ticker, forex เช่น `EURUSD=X`) ดู `../TradingAgents/tradingagents/dataflows/symbols.py`
- เงินใช้ `Decimal` ส่วน weight เก็บเป็นสัดส่วน 0–1 และแปลงเป็น % เฉพาะตอนแสดงผลที่ UI
- Prompt ของแต่ละ role เป็นไฟล์แยกที่มี version เพื่อบันทึกลง provenance
- ห้าม commit `.env` หรือ API key ให้ใช้ `.env.example` และห้ามให้ key ไปโผล่ใน log หรือ provenance

## การทดสอบ

- `domain/compute/`: unit test ทุกฟังก์ชัน เพราะเป็นจุดที่ผิดไม่ได้
- `domain/`: มี test/contract ยืนยันว่าไม่ import framework (F2)
- Graph: ทดสอบ router ทุก branch ด้วย fake LLM ที่คืน structured output ตายตัว รวมถึงกรณี parse ไม่ได้ต้องได้ `REVIEW`, resume จาก checkpoint หลัง node ล้ม และ interrupt → approve/reject
- Harness: ทดสอบ max iteration, loop detection, timeout, retry เฉพาะ error ชั่วคราว, microcompact/compact และการ fallback เมื่อ compaction ล้มเหลว
- Data providers: ใช้ recorded fixtures และทดสอบ failover A → B → C
- Eval คุณภาพคำตอบ research: อิง `../dexter/src/evals/` (dataset CSV + evaluator)

## Commands

รันใน `Product/backend/` ให้ใช้ `uv run python -m <tool>` แทน `uv run <tool>` เพราะถ้า `.venv` ถูกคัดลอกมาจากที่อื่น launcher (`pytest.exe`, `uvicorn.exe`, `mypy.exe`) อาจยังชี้ไป interpreter เดิม (แก้ถาวรได้ด้วย `uv sync --reinstall`)

| งาน | คำสั่ง |
|-----|--------|
| install | `uv sync` |
| dev server (รัน migration อัตโนมัติ) | `uv run python -m uvicorn app.api.main:app --reload --port 8000` |
| migration | `uv run alembic upgrade head` |
| test | `uv run python -m pytest` (live API: `uv run python -m pytest -m live`) |
| lint / format | `uv run ruff check app tests alembic` / `uv run ruff format app tests alembic` |
| typecheck | `uv run python -m mypy app` |
| import contracts (F2) | `uv run lint-imports` |
| LLM smoke | `uv run python -m app.harness.llm.smoke "สวัสดี"` (`--status`, `--tier deep`, `--provider ollama --model gemma:2b`, `--provider claude_code --model claude-haiku-5-5` = subscription ผ่าน Claude Code CLI) |
| export OpenAPI (→ `pnpm gen:api` ใน frontend) | `uv run python -m app.api.export_openapi` → `backend/schemas/openapi.json` |
| export event schema | `uv run python -m app.harness.events.export` → `backend/schemas/events.schema.json` |
| echo run (dev) | `curl -X POST "localhost:8000/api/dev/echo-run?symbol=PTT"` แล้ว `curl -N localhost:8000/api/runs/{run_id}/events` |

Frontend: ยังไม่มีคำสั่งเพิ่มจาก scaffold (ดู `Product/package.json`)
