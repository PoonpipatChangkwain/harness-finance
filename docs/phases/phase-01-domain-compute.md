# Phase 01 — Domain models + deterministic compute (F1, F2)

## เป้าหมาย
สร้าง domain model ทั้งหมดใน contracts และ **ชุดฟังก์ชันคำนวณที่แน่นอน (deterministic)** ที่ LLM ต้องเรียกใช้แทนการคิดเลขเอง ทุกฟังก์ชันเป็น pure function, คืน provenance และมี unit test ที่ใช้ค่าที่คำนวณด้วยมือ

## ต้องเสร็จก่อน
P00

## อ่านก่อนเริ่ม
- `docs/contracts.md` §2 (Provenance), §4 (Symbol), §5 (Portfolio), §8 (Decision)
- `../TradingAgents/tradingagents/dataflows/symbols.py` (normalize_symbol, safe_ticker_component) **port ทั้งไฟล์**
- `../TradingAgents/tradingagents/portfolio.py` (3 สถานะ)
- `../TradingAgents/tradingagents/agents/rating.py` (rating scale + parse)
- `../TradingAgents/tradingagents/dataflows/vendors/yahoo/snapshot.py` (ชุด indicator ที่ต้องมี)
- `../FinRobot/finrobot_equity/core/src/modules/valuation_engine.py` (DCF, EV/EBITDA, peer comps), `sensitivity_analyzer.py`
- `../dexter/src/skills/dcf/SKILL.md`, `sector-wacc.md` (assumptions ของ DCF)

## Tasks

### 1.1 Models (`app/domain/models/`)
- `provenance.py`: `ProvenanceRecord`, `Provenanced[T]` และ helper `new_record(kind, value, *, source, tool=None, calculation=None, params=None, inputs=None, as_of=None, unit=None, url=None) -> ProvenanceRecord` (id ใช้ `prov_` + ULID และ `fetched_at` = now UTC)
- `symbol.py`: `AssetType`, `Market`, `Symbol` และ `normalize_symbol(raw, hint_market=None)` พร้อมกฎ:
  1. strip, uppercase และตัด suffix ของ broker (`+`, `.m`, `-ECN` ฯลฯ ตาม symbols.py)
  2. crypto (`BTCUSD`, `BTC-USDT`) → `BTC-USD`
  3. `XAUUSD` → `GC=F` (COMMODITY), `XAGUSD` → `SI=F`
  4. 6 ตัวอักษรที่เป็น ISO currency 2 ตัว → `EURUSD=X` (FOREX, currency = quote)
  5. ขึ้นต้นด้วย `^` → INDEX
  6. ลงท้าย `.BK` หรือ `hint_market=TH` หรืออยู่ใน `app/domain/data/static/set_symbols.csv` → `.BK` (TH, THB)
  7. นอกนั้นเป็น US stock (USD)
  - `safe_path_component(value, max_len=32)` port จาก `safe_ticker_component`
  - สร้าง `app/domain/data/static/set_symbols.csv` (symbol, name_th, name_en, sector) อย่างน้อย SET50 และ `forex_majors.csv`
- `portfolio.py`: `Position`, `Portfolio`, `PortfolioContext` และ `portfolio_context(p: Portfolio | None) -> PortfolioContext`
  - เพิ่ม field `declared_weight: Decimal | None` ใน Position สำหรับกรณีที่ผู้ใช้กรอกแค่ %
- `decision.py`: `Verdict`, `Rating`, `Decision`, `route_decision()` (fail-safe → REVIEW) และ `parse_rating(text) -> Rating | None` (port rating.py)
- `market.py`: `OHLCVBar`, `OHLCVFrame` (ใช้ pandas ภายในและ serialize เป็น list), `Quote`

### 1.2 Compute (`app/domain/compute/`)
**กฎ:** ทุกฟังก์ชันสาธารณะมี signature `def f(..., *, inputs: list[str] = []) -> ComputeResult[T]` โดย `ComputeResult` มี `value`, `records: list[ProvenanceRecord]` และ `calculation="compute.<module>.<func>@<version>"` ส่วน `inputs` คือ provenance id ของข้อมูลต้นทาง เงินใช้ `Decimal` และสถิติใช้ `float` (ระบุให้ชัดใน type)

`portfolio.py`
- `market_values(positions, prices: dict[str, Decimal], fx: dict[str, Decimal], base_ccy)` → มูลค่าต่อ position ในสกุล base
- `weights(values, cash)` → สัดส่วน 0–1 (รวม cash) โดยรวมต้องเท่ากับ 1 ± 1e-9
- `unrealized_pnl(position, price)` → (abs, pct) และถ้า avg_cost เป็น None ให้คืน None
- `concentration(weights)` → HHI, top1, top3 และ effective N (1/HHI)
- `exposure(weights, by: Literal["sector","asset_type","currency","market"], meta)`
- `rebalance_trades(current_w, target_w, total_value, prices, lot_size: dict[str,int])` → จำนวนหุ้นที่ต้องซื้อ/ขาย ปัดตาม lot (หุ้นไทย lot = 100) พร้อมเงินสดที่เหลือ
- `declared_weights_only(positions)` สำหรับกรณีที่กรอกแค่ %

`returns.py`
- `simple_returns(closes)`, `log_returns(closes)`, `cumulative_return(closes)`
- `annualized_volatility(returns, periods_per_year=252)`
- `max_drawdown(closes)` → (mdd, peak_date, trough_date)
- `beta(asset_ret, bench_ret)`, `correlation_matrix(returns_df)`
- `sharpe(returns, rf_annual)`, `sortino(...)`
- `historical_var(returns, level=0.95)`, `cvar(...)`
- `portfolio_returns(returns_df, weights)`

`indicators.py` (ชุดเดียวกับ snapshot.py บวกเพิ่ม)
- `sma(n)`, `ema(n)`, `rsi(14)` (Wilder smoothing), `macd(12,26,9)` → (macd, signal, hist), `bollinger(20,2)`, `atr(14)` (Wilder), `vwma(20)`, `stoch(14,3)`, `obv`
- `verified_snapshot(frame, as_of)` port `build_verified_market_snapshot`: แถวล่าสุด ≤ as_of, indicator ชุดคงที่, closes ล่าสุด 10 วัน, % เปลี่ยน 1D/5D/1M/3M, 52w high/low และคืนเป็น dict ที่มี provenance ครบ

`risk.py`
- `position_size(equity, risk_pct, entry, stop, lot_size=1)` → จำนวนหน่วยและความเสี่ยงเป็นเงิน
- `stop_from_atr(entry, atr, multiple, side)`
- `risk_reward(entry, stop, target, side)`
- `forex_pip_value(pair, lot_units, account_ccy, rate)` (JPY pair ใช้ pip 0.01 ส่วนอื่นใช้ 0.0001)

`valuation.py` (port แนวคิดจาก valuation_engine.py แต่แยกออกเป็นฟังก์ชัน)
- `DCFAssumptions` (Pydantic, **มีขอบเขต**: wacc 0.03–0.25, terminal_growth −0.02–0.05 และต้อง < wacc, years 3–10, growth ต่อปี −0.5–1.0)
- `dcf(fcf0, assumptions, net_debt, shares)` → EV, equity value, per-share, PV ของแต่ละปี และ terminal share
- `dcf_sensitivity(fcf0, assumptions, wacc_grid, g_grid, ...)` → matrix
- `ev_ebitda_value(ebitda, multiple, net_debt, shares)`, `peer_multiple_stats(peers)` → median/mean/quartiles
- `wacc(rf, beta, erp, cost_of_debt, tax, e_weight)` (CAPM)

### 1.3 Tests (`tests/domain/`)
- symbol: อย่างน้อย 25 เคส (PTT, ptt.bk, AAPL, eurusd, USDJPY, XAUUSD+, BTCUSDT, ^SET.BK, ^GSPC, "../etc" → safe path)
- portfolio: weights รวมเป็น 1, cash None ≠ 0, 3 สถานะของ PortfolioContext, rebalance ที่ lot 100 และเงินสดไม่ติดลบ
- indicators: เทียบกับค่าที่คำนวณมือ/ค่าอ้างอิงจาก fixture CSV ที่เตรียมไว้ใน `tests/fixtures/ohlcv_ptt.csv` (RSI/ATR แบบ Wilder ต้องตรงถึงทศนิยม 6 ตำแหน่ง)
- returns/risk: max drawdown บนชุดข้อมูลที่รู้คำตอบ และ position size
- valuation: DCF บนตัวอย่างที่คำนวณด้วยมือ และ assumptions นอกขอบเขตต้อง raise `ValidationError`
- ทุก compute ต้องคืน record ที่ `calculation` ไม่ว่าง และ `inputs` ถูกส่งต่อ
- ใช้ `hypothesis` (เพิ่ม dev dep) กับ weights/rebalance เพื่อตรวจว่าผลรวม ≈ 1 และไม่มีเงินสดติดลบ

## Definition of Done
- [ ] `app/domain/**` ผ่าน `mypy --strict`
- [ ] coverage ของ `app/domain/compute` ≥ 95%
- [ ] `lint-imports` ผ่าน (domain ไม่ import framework)
- [ ] ไม่มีฟังก์ชันใดใน compute เรียก network/LLM/IO
- [ ] ทุกไฟล์ที่ port มี docstring ระบุที่มา

## นอกขอบเขต
การดึงข้อมูลจริง (P02) และ LLM
