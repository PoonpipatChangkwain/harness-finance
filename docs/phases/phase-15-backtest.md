# Phase 15 — Backtest + จำลองพอร์ต (Portfolio simulator)

> สถานะ: ✅ 15.1–15.4 เสร็จ (2026-10-01) · contracts.md อัปเดตแล้ว · ค่าที่ตัดสินใจ: commission default 0 (ผู้ใช้กรอก), cadence auto = intraday 1 / swing 3 / position 5 วันทำการ

## เป้าหมาย
ตอบคำถามว่า **"ถ้าให้ระบบเทรดตัดสินใจเองในช่วงวันที่ X–Y ด้วยเงินต้น N บาท พอร์ตจะไปจบที่เท่าไหร่"**
- รัน trading workflow เดิมซ้ำบนวันที่ในอดีตหลายวัน หลาย symbol (look-ahead safe)
- จำลองการเข้า/ออกออเดอร์จากแท่งราคาจริงหลังวันตัดสินใจ: ชน stop, ชน target, time stop, ออเดอร์ไม่ fill
- มีพอร์ตจำลอง: เงินต้น, เงินสด, ขนาด position คิดจาก equity ณ ตอนนั้น, ค่าธรรมเนียม, slippage, lot size
- ได้ equity curve, drawdown, รายการเทรด, metric และเทียบกับ benchmark (buy & hold และกลยุทธ์ baseline ที่ไม่ใช้ LLM)
- **เปลี่ยนเงินต้นหรือ risk แล้วจำลองใหม่ได้ทันที โดยไม่ต้องเรียก LLM ซ้ำ**

ไม่ทำ: ส่งออเดอร์จริงหรือ paper trading กับ broker (ยังเป็น follow-up ที่ต้องมี gate แยก)

## ต้องเสร็จก่อน
P10–P13 (เสร็จแล้ว) — ทำก่อนหรือคู่กับ P14 ได้ และงานนี้ **แทน** หัวข้อ `evals/trading_backtest.py` ใน 14.1

## อ่านก่อนเริ่ม
- `../TradingAgents/tradingagents/backtest.py` — grid (symbol × date), `iter_grid`, สรุปผลจาก decision log
- `../TradingAgents/tradingagents/graph/settlement.py` — benchmark ตามตลาด (`resolve_benchmark`), `resolution_date` = จุดตัด point-in-time ของ lesson (#1251), ไม่ settle ถ้าช่วงถือยังเทรดไม่ครบ (#1169)
- `backend/app/domain/tools/trading.py` (`size_trade`), `domain/workflows/trade_outcome.py` (`realized_r`), `domain/data/window.py`

---

## หลักการออกแบบ

### 1. แยก 2 ชั้น: "ตัดสินใจ" ≠ "จำลองพอร์ต"

TradingAgents เลือกไม่ทำ portfolio simulator เพราะการตัดสินใจไม่มี quantity, ราคา fill และบัญชีเงินสด ระบบเราแก้ปัญหานี้โดยแยกเป็น 2 ชั้นที่ไม่ปนกัน:

```
┌─ ชั้น A: Decision generation (แพง, ใช้ LLM, ทำครั้งเดียว) ──────────────┐
│ for (symbol, date) in grid:                                               │
│   trading graph เดิม (analysis_date=date, mode=backtest)                  │
│   → decision card: side, order type, stop, targets, size_fraction,        │
│     time_stop_bars, interval  (ไม่ผูกกับเงินต้น)                            │
│   → เก็บใน backtest_cells (cache ตาม signature)                            │
└──────────────────────────────┬────────────────────────────────────────────┘
                               │ decisions[]  (ไม่มีจำนวนหุ้น)
                               ▼
┌─ ชั้น B: Portfolio simulator (ฟรี, deterministic, Decimal, ไม่มี LLM) ─────┐
│ SimConfig: เงินต้น, สกุลเงิน, risk %, max weight, max positions,          │
│            fee, slippage, allow short, intrabar rule                      │
│ เดินทีละแท่ง → fill / stop / target / time stop → ledger + equity curve   │
│ → metrics + benchmark                                                     │
└───────────────────────────────────────────────────────────────────────────┘
```

- การตัดสินใจของ LLM **ไม่ขึ้นกับเงินต้น** เพราะ LLM เสนอแค่ทิศทาง ระดับ stop/target และ keep-ratio (`size_fraction`) ส่วนจำนวนหุ้นคำนวณใหม่ในชั้น B จาก equity จำลอง ณ วันนั้น ด้วยสูตรเดียวกับ `size_trade` (F1)
- ผลคือ: เปลี่ยนเงินต้นจาก 100,000 เป็น 1,000,000 หรือเปลี่ยน risk 1% → 2% แล้ว **simulate ใหม่ได้ในไม่กี่วินาที** ไม่เสียค่า LLM
- ชั้น B ทดสอบได้ด้วย decision ปลอม (ไม่ต้องมี LLM เลย) และใช้รันกลยุทธ์ baseline ได้ (ดู §6)

### 2. Look-ahead safety เป็นเรื่องของ harness ไม่ใช่ prompt

| ช่องทางรั่ว | สถานะตอนนี้ | แก้ใน phase นี้ |
|------------|-------------|----------------|
| แท่งราคาหลัง `as_of` | ตัดแล้ว (`frame.as_of(cutoff)`) | ย้ายการตัดไปที่ data router ของ backtest (§3) ให้ tool ใดก็หลุดไม่ได้ |
| ช่วงราคาที่ดึง | `_snapshot` ดึง `INTERVAL_RANGE` (เช่น 2y) **นับจากวันนี้** แล้วค่อยตัด → `as_of` ที่เก่ากว่า ~2 ปีได้แท่งไม่พอสำหรับ SMA200 หรือว่างเลย | เพิ่ม `start`/`end` ใน capability `PRICES` แล้ว backtest preload ตามช่วงที่ต้องใช้ |
| ข่าว / social | กรองด้วย `in_window` แล้ว แต่ feed ส่วนใหญ่ให้แค่ข่าวล่าสุด → ในอดีตได้ "unavailable" | default analysts ของ backtest = `market` (+`fundamentals` สำหรับหุ้น) และรายงาน coverage |
| งบการเงิน | ยังไม่ได้ audit (P14.2) | ต้องผ่าน look-ahead audit ของ STATEMENTS ก่อนเปิด fundamentals analyst ใน backtest |
| Lessons จาก decision log | `MemoryDecisionLog.lessons()` **ไม่กรองตามวันที่** → run ย้อนวันเห็นบทเรียนที่เกิดทีหลัง | กรอง `settled_at < analysis_date` (แก้ให้ live run ย้อนวันด้วย) และ backtest ไม่อ่าน/ไม่เขียน `trade_decisions` |
| Entry ใช้ราคาปิดของวันตัดสินใจ | backtest ใช้ snapshot close เป็น entry | simulator fill ที่ **open ของแท่งถัดไป** (ราคาปิดรู้ได้ตอนตลาดปิดเท่านั้น) |
| ความรู้ในตัวโมเดล | กันไม่ได้ (โมเดลอาจจำราคาในอดีตได้) | รายงานเป็นคำเตือนในผล + แนะนำช่วงวันที่หลัง training cutoff ของโมเดลที่ใช้ |

### 3. Backtest data router: preload ครั้งเดียว แล้วตัดตาม as_of ทุก call

- ก่อนเริ่ม preload แท่งของทุก symbol ตลอดช่วง `[start − warmup, end + max holding]` (warmup ≥ 250 แท่ง) และ FX ของคู่ที่ต้องแปลงสกุลเงิน
- ระหว่างรัน cell วันที่ D ให้ `DataRouter` แบบ override ตอบ `PRICES`/`QUOTE` จาก frame ใน memory ที่ตัดที่ D เท่านั้น
  - ไม่ยิง Yahoo ซ้ำหลายร้อยครั้ง (rate limit)
  - tool ไหนลืมส่ง `as_of` ก็ยังรั่วไม่ได้
  - provenance บันทึก `source=yfinance` และ `as_of=D` ตามเดิม
- ชั้น B ใช้ frame เดียวกัน (รวมแท่งหลัง D) ซึ่งเป็นที่เดียวที่อนุญาตให้เห็นอนาคต

**ข้อจำกัดของข้อมูล (Yahoo):**

| Horizon | Interval | ช่วงย้อนหลังสูงสุดที่ทำได้ |
|---------|----------|---------------------------|
| intraday | 15m | ~60 วัน (ลบ warmup เหลือใช้ได้ราว 1 เดือน) |
| swing | 1h | ~2 ปี |
| position | 1d | หลายปี |

ถ้าช่วงที่ขอเกินขีดจำกัด → validation error ตอนสร้าง backtest พร้อมบอกช่วงที่ทำได้ (ห้าม fallback เงียบๆ)

---

## ชั้น A: Decision generation

### Grid
- `symbols`: 1–10 ตัว (หุ้นไทย/US/forex ผสมได้)
- `start_date`, `end_date`, และ `cadence` = ทุกกี่แท่งของ interval ระดับวัน (default: position = ทุก 5 วันทำการ, swing = ทุกวันทำการ, intraday = ทุกวันทำการ)
- ใช้วันทำการจริงจากแท่งที่ preload มา (ไม่ใช่ปฏิทิน) → ไม่มี cell วันหยุด
- ไม่เกินวันที่ที่ยังมีแท่งพอให้ position ปิดได้ (ตาม #1169)

### `mode=backtest` ของ trading graph
เพิ่ม field ใน `TradeRunInput` (ภายใน ไม่เปิดให้ `/trade/runs`): `backtest_id: str | None`

| Node | พฤติกรรมเมื่อ `backtest_id` มีค่า |
|------|----------------------------------|
| `load_context` | `use_portfolio=false` (พอร์ตจำลองอยู่ชั้น B), event risk = unavailable (มีอยู่แล้ว), lessons ตาม `learn_mode` (ด้านล่าง) |
| `refresh_price` | ไม่เรียก live quote (มีอยู่แล้ว) |
| `approval` | **auto**: policy pass + verdict APPROVE → `approved`; REVIEW → ตาม `review_policy` (default `skip`); policy block → `rejected` ไม่ interrupt |
| `record_decision` | เขียนลง `backtest_cells` ไม่ใช่ `trade_decisions` |
| `report` | สั้นลง (ไม่ต้องสร้าง markdown เต็ม) เพื่อลด token |

- `default_notional` ใช้ค่าคงที่ (sizing จริงไปทำใน B) → card เก็บ `size_fraction`, `risk_pct`, ระยะ stop ไว้ให้ B คำนวณใหม่
- graph signature ของ checkpoint ต้องรวม `backtest_id` เพื่อไม่ให้ resume ปนกับ live run

### Cache / reuse
key ของ cell = `hash(symbol, date, horizon, analysts, debate_rounds, risk_rounds, deep_model, quick_model, prompt_versions, learn_mode)`
- backtest ใหม่ที่ตั้งค่าเหมือนเดิมใช้ decision เดิมได้ (ไม่เสียเงินซ้ำ) และ UI บอกว่า cell ไหนมาจาก cache
- เปลี่ยน prompt version → cache miss อัตโนมัติ

### Lessons ระหว่าง backtest (`learn_mode`)
| ค่า | ความหมาย | ต้นทุน |
|-----|----------|--------|
| `off` (default) | ทุก cell อิสระต่อกัน เป็นการวัดคุณภาพการตัดสินใจที่ "สะอาด" | ถูกสุด, รัน cell พร้อมกันได้ |
| `in_backtest` | cell วันที่ D เห็นเฉพาะ lesson ของเทรดที่ **ปิดแล้วก่อน D** ใน backtest นี้ (simulate เทรดนั้นทันทีด้วย config เริ่มต้น แล้วเขียน reflection) | ต้องรันเรียงวัน + reflection call ต่อเทรด |

ห้ามใช้ lesson จาก live log ใน backtest เด็ดขาด

### Concurrency, cost guard, resume
- รัน cell พร้อมกันได้ `concurrency` ตัว (default 2, สูงสุด 4) เฉพาะ `learn_mode=off`
- **ประเมินต้นทุนก่อนเริ่ม:** `POST /backtests/estimate` คืนจำนวน cell, จำนวน cell ที่มี cache, และค่าใช้จ่ายโดยประมาณจาก usage เฉลี่ยของ trade run ที่ผ่านมาด้วย config ใกล้เคียง (ถ้าไม่มีประวัติ → บอกว่าประเมินไม่ได้) UI ต้องให้ผู้ใช้กดยืนยัน
- `budget_usd` ต่อ backtest: เกินแล้วหยุด (cancel อย่างนุ่มนวล) และยังจำลองจาก cell ที่เสร็จแล้วได้
- resume: cell ที่มี decision แล้วข้าม, cell ที่ค้างใช้ checkpoint ของ trading graph เดิม (T3)
- cell ที่ fail (provider down, LLM error) → บันทึกสถานะและเหตุผล ไม่หยุดทั้ง backtest และรายงานใน coverage

---

## ชั้น B: Portfolio simulator (`domain/compute/backtest/`)

pure functions, `Decimal`, มี unit test ทุกกรณี (F1) ไม่ import harness

### SimConfig

| Field | Default | หมายเหตุ |
|-------|---------|----------|
| `initial_capital` | 100,000 | **เงินต้นพอร์ต** |
| `base_currency` | THB | position ในสกุลอื่นแปลงด้วย FX close ของวันนั้น |
| `risk_pct` | 0.01 | ส่วนของ equity ที่ยอมเสียถ้าชน stop (× `size_fraction` จาก PM) |
| `max_position_weight` | 0.20 | notional ต่อ position / equity |
| `max_open_positions` | 5 | |
| `max_gross_exposure` | 1.0 | หุ้นไม่มี margin = ห้ามเกินเงินสด; forex ใช้ `leverage` |
| `leverage` | 1 (หุ้น), 10 (forex) | |
| `compounding` | true | false = sizing จากเงินต้นเสมอ |
| `allow_short` | false หุ้นไทย / true forex | หุ้นไทยในบัญชีเงินสดชอร์ตไม่ได้ |
| `commission` | กำหนดต่อตลาด (สมมติฐาน แก้ได้) | ต้องแสดงใน UI ว่าเป็นค่าสมมติ |
| `slippage_bps` | 5 | ใช้กับ market order และ stop |
| `spread_pips` | ต่อคู่เงิน | forex |
| `intrabar_rule` | `stop_first` | แท่งเดียวชนทั้ง stop และ target → ถือว่าชน stop (มองแง่ร้าย) |
| `scale_out` | `first_target` | หรือ `equal_split` ปิดทีละส่วนตาม target |
| `on_existing_position` | `ignore_same_side` | สัญญาณฝั่งตรงข้าม → ปิดที่ open ถัดไปก่อน แล้วค่อยพิจารณาเปิดใหม่ |

### ลำดับต่อแท่ง (event-driven, deterministic)

```
for bar in timeline (รวมทุก symbol เรียงตามเวลา):
  1. gap ที่ open: position ที่ open ทะลุ stop/target → ปิดที่ราคา open (ไม่ใช่ราคา stop)
  2. intrabar: high/low ชน stop/target → ปิดที่ระดับนั้น ± slippage
     (ชนทั้งคู่ในแท่งเดียว → ตาม intrabar_rule)
  3. time stop ครบ → ปิดที่ close
  4. pending order (limit/stop): แท่งแตะราคา → fill; หมด order_valid_bars → expired
  5. decision ใหม่ของวันก่อนหน้า → market order fill ที่ open ของแท่งนี้
     - open ทะลุ stop หรือ target ไปแล้ว → skip "gap_invalidated"
     - sizing: units = (equity × risk_pct × size_fraction) / |fill − stop|
       → cap ด้วย max_position_weight, เงินสด/margin, max_open_positions
       → ปัด lot ลง (หุ้นไทย 100)  → ได้ 0 → skip "below_one_lot"
  6. mark-to-market ที่ close → equity, cash, exposure (แปลงเป็น base_currency)
```

- position ที่ยังเปิดอยู่ตอนจบช่วง → ปิดที่ close สุดท้าย ป้าย `end_of_test`
- ระยะ stop/target ใช้ระดับราคาจาก card ตรงๆ แล้วคิด R จากราคา fill จริง (ใช้ `realized_r` เดิม)
- intraday: ใช้แท่ง 15m/1h ตาม interval ของ card ในข้อ 1–4 ส่วน daily ใช้เป็นแกนเวลาของ equity curve

### ทำไมเงินต้นมีผลจริง (ตัวอย่าง)
หุ้นไทยราคา 34 บาท, stop ห่าง 1 บาท, risk 1%, max weight 20%, lot 100:

| เงินต้น | งบเสี่ยง | units จาก risk | cap 20% | หลังปัด lot | ผล |
|---------|---------|---------------|---------|-------------|-----|
| 10,000 | 100 | 100 | 58 | **0** | skip `below_one_lot` |
| 100,000 | 1,000 | 1,000 | 588 | 500 | เข้าได้ แต่ถูก cap โดย weight |
| 1,000,000 | 10,000 | 10,000 | 5,882 | 5,800 | เข้าได้ |

พอร์ตเล็กจึงข้ามเทรดได้เยอะและผลต่างจากพอร์ตใหญ่จริง ผลต้องแสดงจำนวน skip แยกตามเหตุผลเสมอ

### Output
- `ledger[]`: symbol, side, decision date, run_id, fill/exit time & price, units, fee, slippage, P&L (instrument + base ccy), R, exit reason (`tp|sl|time_stop|gap|reverse|end_of_test`)
- `skipped[]`: decision ที่ไม่ได้เทรด + เหตุผล (`flat`, `review_skipped`, `policy_rejected`, `below_one_lot`, `max_positions`, `no_cash`, `gap_invalidated`, `order_expired`, `short_not_allowed`, `cell_failed`)
- `equity_curve[]`: date, equity, cash, exposure, drawdown
- `metrics` (ทุกตัวเป็น `Provenanced`, calculation `compute.backtest.*@v1`):
  - เงิน: final equity, total return, CAGR, max drawdown (% และจำนวนวัน), Sharpe, Sortino, exposure %, ค่าธรรมเนียมรวม
  - เทรด: จำนวนเทรด, win rate, avg win / avg loss, profit factor, expectancy (R), avg holding
  - คุณภาพการตัดสินใจ (ไม่ขึ้นกับเงินต้น): hit rate ของ decision ที่ไม่ flat, avg R ต่อ decision, rating distribution
- `benchmarks`: buy & hold ของแต่ละ symbol (แบ่งเงินเท่ากัน), ดัชนีตามตลาด (`^SET.BK` สำหรับ `.BK`, `SPY` สำหรับ US; forex ไม่มี) และกลยุทธ์ baseline (§6) → alpha

---

## 6. Baseline strategy (ไม่ใช้ LLM)

เพื่อตอบว่า "LLM ช่วยจริงไหม" ให้มี decision generator แบบ rule-based ที่สร้าง card รูปแบบเดียวกับชั้น A เช่น SMA20/50 cross + stop 2×ATR + target 2R แล้วรันผ่าน simulator เดียวกันด้วย SimConfig เดียวกัน
- ใช้เป็น test fixture ของ simulator ได้ด้วย (ไม่ต้องใช้ LLM)
- แสดงคู่กับผล LLM ในกราฟเดียวกัน

---

## Storage (migration 0005)

| ตาราง | คอลัมน์หลัก |
|-------|------------|
| `backtests` | id, name, config (JSON: grid + generation), status (`estimating/running/paused/completed/failed/cancelled`), usage/cost, created_at |
| `backtest_cells` | backtest_id, cache_key, symbol, date, run_id, status, card (JSON), verdict/rating, usage, error |
| `backtest_sims` | id, backtest_id, sim_config (JSON), metrics (JSON), ledger (JSON), skipped (JSON), equity_curve (JSON), engine_version, created_at |

cell อ้าง `run_id` ของ trading run จริง → คลิกดู pipeline, debate และ provenance ของวันนั้นได้ในหน้า `/trade/{runId}` เดิม

## Workflow `backtest` (F5)

```
validate_config → preload_data → generate_decisions → simulate → benchmarks → report
                                  (loop cells, resumable)   (ชั้น B, SimConfig แรก)
```
- `generate_decisions` เรียก trading graph เป็น child run ผ่าน `GraphRunner` ที่มีอยู่ ไม่ต้องแก้ `harness/`
- events: `backtest.progress {done, total, cached, failed, cost_usd}`, `backtest.cell {symbol, date, status, rating, run_id}`, `backtest.sim_completed {sim_id, metrics}` (เพิ่มใน contracts §10)

## API

| Method | Path | ทำอะไร |
|--------|------|--------|
| POST | `/api/backtests/estimate` | จำนวน cell, cached, ประมาณค่าใช้จ่าย, ตรวจข้อจำกัดช่วงข้อมูล |
| POST | `/api/backtests` | สร้างและเริ่ม (config + SimConfig แรก) → 202 `{id, run_id}` |
| GET | `/api/backtests`, `/api/backtests/{id}` | รายการ / รายละเอียด + cells + sims |
| GET | `/api/runs/{run_id}/events` | SSE เดิม |
| POST | `/api/backtests/{id}/cancel`, `/resume` | |
| POST | `/api/backtests/{id}/simulate` | **SimConfig ใหม่ → ผลทันที** (sync, ไม่มี LLM) |
| GET | `/api/backtests/{id}/sims/{sim_id}` | |
| DELETE | `/api/backtests/{id}` | ลบ backtest + sims (ไม่ลบ trading run ที่ cache อ้างอยู่) |

## UI `/backtest/[[...id]]`

1. **Setup** — symbols (multi, ใช้ SymbolSearch เดิม), ช่วงวันที่ (บอกขีดจำกัดตาม horizon), horizon, cadence, analysts, model tier, `learn_mode`, `review_policy`, budget; **เงินต้น + สกุลเงิน**, risk %, max weight, max positions, fee/slippage (ระบุว่าเป็นค่าสมมติ), allow short → ปุ่ม "ประเมิน" แสดง cell/cached/ค่าใช้จ่าย → ยืนยันเริ่ม
2. **Progress** — grid symbol × วันที่ แต่ละช่องสีตาม rating/สถานะ (คลิก → `/trade/{runId}`), แถบ progress + cost สะสม, ปุ่ม pause/cancel
3. **ผลลัพธ์**
   - tiles: เงินต้น → เงินสุดท้าย, return, CAGR, max DD, Sharpe, win rate, จำนวนเทรด / skip
   - equity curve (lightweight-charts) เทียบ buy & hold, ดัชนี, baseline + กราฟ drawdown ด้านล่าง
   - **What-if panel:** เปลี่ยนเงินต้น/risk/fee แล้ว simulate ใหม่ทันที เก็บเป็น sim แยกและเทียบกันได้
   - ตารางเทรด (TanStack) + ตาราง skip แยกตามเหตุผล
   - marker บนกราฟราคาของแต่ละ symbol: จุดเข้า/ออก, stop/target
   - **แถบคำเตือนถาวร:** ผลในอดีตไม่รับประกันอนาคต, โมเดลอาจรู้เหตุการณ์ในช่วงที่ทดสอบ, ค่าธรรมเนียมเป็นค่าสมมติ, coverage ของข่าว/งบ
4. sidebar: รายการ backtest เดิม

---

## Tasks (เรียงตาม dependency)

### 15.1 Prerequisites (แก้ของเดิม) — ✅ 2026-10-01
- [x] yfinance `PRICES`: `as_of` ในอดีต → ดึง `history(start, end)` ช่วงที่**จบที่ as_of** (แทน `period=` ที่จบวันนี้); `common.fetch(..., as_of=)` และ `_snapshot`/`compute_indicators` ส่ง `as_of` ลงไป
- [x] **(พบระหว่างทำ)** แท่งรายวันของตลาดเอเชียลงวันผิด: Yahoo ประทับแท่ง SET ที่เที่ยงคืนเวลาไทย = 17:00 UTC ของวันก่อน → `ts.date()` ได้วันก่อนหน้า ทำให้ `as_of` วันพุธเห็นแท่งวันพฤหัส (look-ahead 1 วัน) และกราฟ/as-of ของ live run เลื่อนไป 1 วัน — แก้ใน `_bar_ts` (แท่ง 1d/1wk ประทับ 00:00 UTC ของวันที่ local) + test
- [x] `MemoryDecisionLog.lessons(..., before=)` กรอง `settled_at < analysis_date` (settlement ที่ไม่มีวันที่ถูกตัดทิ้งใน run ย้อนวัน) — `load_context` ส่ง `analysis_date` ให้แล้ว จึงแก้ live run ย้อนวันไปด้วย
- [x] `domain/data/backtest.py`: `PriceStore` + `preload_prices` + `PointInTimeRouter` (PRICES ตัดที่ as_of และ trim ตาม range, QUOTE = close ล่าสุด ≤ as_of, capability อื่น clamp `as_of`, symbol ที่ไม่ได้ preload → `NoData`) และ `registry.use_router()` (ContextVar ต่อ task) — test ยืนยันว่า `get_prices` ซึ่ง**ไม่มี** arg `as_of` ก็ไม่เห็นอนาคต

### 15.2 Simulator (ชั้น B) — ✅ 2026-10-01
- [x] `domain/compute/backtest/`: `models.py` (SimDecision/SimConfig/Trade/Skipped/EquityPoint/SimResult), `engine.py` (`simulate`), `metrics.py` (`performance`, `buy_and_hold`), `baseline.py` (`sma_cross`)
- [x] 27 unit tests (`tests/domain/compute/test_backtest_sim.py`), coverage 98%, ตัวเลขทุกตัวคำนวณมือไว้ใน comment
- [x] ทดสอบกับข้อมูลจริง (PTT.BK, SMA20/50, 2024-10 → 2026-09): ทุนหมื่น = 0 เทรด (ติด lot 100 ทั้ง 7 ครั้ง), ทุนแสน/ล้าน = 7 เทรด ≈ −1.8% เทียบ buy & hold +19.9% — ผลของ baseline นี้ไม่ได้ใช้ตัดสินอะไร แค่ยืนยันว่า pipeline ทำงานกับข้อมูลจริง

**พฤติกรรมที่ตัดสินใจระหว่าง implement (ต่างจาก/ละเอียดกว่าร่างด้านบน):**
- `commission_pct` default **0** (ยังรอผู้ใช้ให้ตัวเลขต่อตลาด) — UI ต้องบังคับให้เห็นว่าไม่มีค่าธรรมเนียม; `slippage_bps` default 5 และใช้แทน spread ของ forex ด้วย (ไม่มี `spread_pips` แยก)
- decision `flat` = ไม่ทำอะไร (position ที่เปิดอยู่ใช้ stop/target/time stop ของตัวเอง); decision ฝั่งตรงข้าม **ปิด position ที่ open ก่อนเสมอ** แม้จะเปิดฝั่งใหม่ไม่ได้ (เช่นหุ้นไทยได้ SELL → ปิด long แล้ว skip `short_not_allowed`)
- แท่งที่ pending order fill: เช็คแค่ stop (ไม่รู้ว่าใน bar นั้น target มาก่อนหรือหลัง fill) — market fill ที่ open เช็คทั้งคู่ตาม `intrabar_rule`
- position ที่ชน `end_of_test` ปิดก่อน symbol อื่นใน timestamp เดียวกันจะถูกประมวลผล (ลำดับ symbol ตามตัวอักษร) ผลต่อ buying power มีเฉพาะแท่งสุดท้าย
- บัญชีแบบ margin เดียว: equity = cash + unrealized, margin = notional/leverage (leverage 1 = บัญชีเงินสด), `cash` ใน equity curve = equity − margin
- `buy_and_hold` ใช้ units เศษส่วน (วัดตลาด ไม่ใช่การปัด lot)

**ยังเหลือ (ส่งต่อ 15.3):** `get_prices` และ tool ใน `analysis.py` ไม่มี arg `as_of` — ใน backtest ปลอดภัยเพราะ `PointInTimeRouter` แต่ถ้า run ย้อนวันผ่านหน้า `/trade` ตรงๆ (ไม่ผ่าน backtest) analyst ยังเรียกแท่งล่าสุดได้ → 15.3 ควรให้ trading run ที่มี `analysis_date` ใช้ `PointInTimeRouter` เสมอ ไม่ใช่เฉพาะใน backtest

### 15.3 Decision generation (ชั้น A) + workflow + API — ✅ 2026-10-01
- [x] `TradeRunInput.backtest_id` (อยู่ใน `signature_fields`; `POST /trade/runs` ที่ส่งมา → 400) → harness ให้ `auto_approve` (APPROVE → approved, REVIEW/อื่น → rejected; policy ยังตรวจตามปกติ) + `NullDecisionLog` (ไม่อ่าน/เขียน log จริง)
- [x] trading run ที่มี `analysis_date` ในอดีตรันหลัง `PointInTimeRouter` เสมอ (`point_in_time()` ใน `harness/workflow/trading.py`) → ปิดช่องรั่วของ `get_prices` ที่หน้า `/trade` ด้วย; preload ใช้ `start` ชัดเจน + `YAHOO_MAX_DAYS` (15m ~58 วัน, 1h ~725 วัน)
- [x] `domain/workflows/backtest.py` (`BacktestConfig`, `grid`, `cell_decision`, `sim_decision`) + `harness/workflow/backtest.py` (workflow `backtest`: preload → cells (cache_key, concurrency, budget, cancel ลูก, resume = รันซ้ำ ข้าม cell ที่เสร็จ) → `simulate_backtest` (+ buy & hold + SMA baseline), `estimate`); ตลาดที่ preload แล้ว cache ใน process (4 backtest) ให้ re-simulate ทันที
- [x] migration 0005 (`backtest_cells`, `backtest_sims`), `backtest_repo`, events `backtest.progress/cell/sim`, API `/api/backtests*`, `schemas/*.json` + `pnpm gen:api`
- [x] contracts.md §10/§11/§12
- [x] tests: `tests/harness/trading/test_backtest_workflow.py` 7 ตัว (end-to-end FakeLLM 2 symbol, cache, resume หลัง crash รันเฉพาะ cell ที่หาย, cell ล้มไม่หยุดทั้งหมด, budget, API, mapping)

### 15.4 UI — ✅ 2026-10-01
- [x] หน้า `/backtest/[[...id]]` + nav "Backtest" + sidebar `BacktestHistory`; `BacktestSetup` (symbols chips, ช่วงวัน, horizon/cadence, analysts, rounds, model, concurrency, งบ, baseline, **พอร์ตจำลอง** `SimFields` + เตือนเมื่อค่าคอมฯ = 0; ต้องกด "ประเมิน" ก่อนเริ่ม), `CellGrid` (คลิก → `/trade/{runId}`), `ResultTiles`, `EquityChart` (Recharts: ระบบ vs buy & hold vs SMA, เงิน/%, drawdown), `WhatIfPanel` (จำลองใหม่ + ตารางเทียบ sim), `TradesTable` + สรุปเหตุผลที่ไม่ได้เทรด, กล่องคำเตือน
- [x] `lib/backtest.ts` + `lib/backtest-api.ts`; tests 15 ตัว (`lib/__tests__/backtest.test.ts`, `components/backtest/__tests__`)

### ผลทดสอบจริง (2026-10-01, Ollama Cloud deepseek-v4-pro/gemma4, PTT position, 1–22 ก.ย. ทุก 5 วัน)
- 4 cell เสร็จ ~8 นาที (concurrency 2), snapshot ของ cell 1 ก.ย. = แท่ง 1 ก.ย. (ไม่รั่ว), auto-approved
- ผล **0 เทรด**: trader สั่ง buy limit ต่ำกว่าตลาดทุกครั้ง (40.25 / 40.85 / 41.50 / 41.60) แต่ราคาขึ้นไม่ย่อลงมาถึง → order ค้างจนถูกแทน/หมดช่วง; buy & hold +1.2% — ตรวจกับแท่งจริงแล้ว simulator ถูก นี่คือพฤติกรรมของโมเดล

**ข้อจำกัดที่รู้แล้ว:** การจำลองหยุดที่ `end_date` (position ที่ยังเปิดปิดแบบ `end_of_test`, การตัดสินใจวันสุดท้ายไม่มีแท่งให้เข้า → `no_bars`); `estimate` นับวันธรรมดา (ไม่รู้วันหยุดตลาด); ตลาดที่ preload เก็บใน memory ของ process (restart แล้ว simulate ครั้งแรกจะโหลดราคาใหม่)

## Definition of Done
- [ ] simulator: coverage ≥ 95%, ผลซ้ำได้ (input เดิม → ผลเดิมทุกหลัก)
- [ ] look-ahead audit: แทรกแท่ง/ข่าว/lesson ในอนาคตไว้ใน fixture แล้ว backtest ต้องไม่เห็น
- [ ] backtest จริงด้วย FakeLLM: 2 symbol × 10 วัน จบ, kill กลางทาง → resume ไม่รัน cell ที่เสร็จซ้ำ
- [ ] live: PTT.BK + AAPL ช่วง 3 เดือน horizon position ด้วย groq จบได้ภายใน budget, เปลี่ยนเงินต้น 3 ค่าแล้วได้ผลภายใน 2 วินาทีโดยไม่มี LLM call
- [ ] ruff / mypy --strict / import-linter / frontend lint+typecheck+test ผ่าน

## คำถามที่ยังต้องตัดสินใจ
1. Default cadence ของ swing: ทุกวันทำการ (แม่นแต่แพง) หรือทุก 2–3 วัน?
2. รองรับ backtest แบบ intraday ตั้งแต่เวอร์ชันแรกไหม (ข้อมูลย้อนได้แค่ ~1 เดือน)
3. ค่า commission default ต่อตลาด (ไทย/US/forex) จะใช้ตัวเลขไหน — ต้องให้ผู้ใช้ยืนยัน ไม่ควรเดา
