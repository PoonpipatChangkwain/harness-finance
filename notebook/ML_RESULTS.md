# XAUUSD Bull/Bear ML — Experiment Results & Roadmap

สรุปผลทดลองทุกเวอร์ชัน เก็บไว้เป็น reference สำหรับการปรับปรุงในอนาคต

---

## สรุปเปรียบเทียบทุกเวอร์ชัน (Holdout Aug-Sep 2026, Intraday 15m)

| Version | Architecture | Trades | Win% | PF | Exp_R | Total_R | Long% | Short% |
|---------|-------------|--------|------|----|-------|---------|-------|--------|
| v0.2 E3 | LightGBM pair, reduced features | — | — | — | — | — | ~99% | ~1% |
| v0.3 F3 | LightGBM pair + VIX/DXY/US10Y | 25 | 44% | 0.88 | -0.07 | -1.7R | 100% | 0% |
| v0.4 | v0.3 + regime + class balancing | — | — | — | — | — | ~85% | ~15% |
| **v0.5 C** | **Stacked ensemble + conf>0.40** | **199** | **45%** | **1.15** | **+0.08** | **+15.6R** | **88%** | **12%** |

## v0.5 ละเอียด — Holdout Aug-Sep 2026

### Intraday (15m)

| Experiment | Trades | Win% | PF | Exp_R | Total_R | Max DD | Long | Short |
|-----------|--------|------|----|-------|---------|--------|------|-------|
| A: specialist + tau/m | 25 | 44.0% | 0.88 | -0.068 | -1.7R | 4.9% | 25 | 0 |
| B: meta-model (no filter) | 231 | 44.2% | 1.09 | +0.051 | +11.8R | 16.9% | 200 | 31 |
| **C: meta (conf>0.40)** | **199** | **45.2%** | **1.15** | **+0.078** | **+15.6R** | **13.2%** | **176** | **23** |
| D: meta (conf>0.50) | 121 | 42.1% | 1.03 | +0.015 | +1.8R | 10.9% | 114 | 7 |
| always_long | 476 | 40.5% | 0.95 | -0.030 | -14.1R | 37.5% | 476 | 0 |
| always_short | 484 | 41.3% | 0.93 | -0.040 | -19.2R | 37.5% | 0 | 484 |

**Monthly breakdown (Exp C):**
- Aug 2026: 100 trades, win 51%, PF 1.43, **+21.4R**
- Sep 2026: 99 trades, win 39%, PF 0.90, **-5.8R**

**Holdout regime: SIDE 93%, DOWN 7%, UP 0%**

### Swing (1h)

| Experiment | Trades | Win% | PF | Exp_R | Total_R | Long | Short |
|-----------|--------|------|----|-------|---------|------|-------|
| A: tau/m | 53 | 39.6% | 1.12 | +0.074 | +3.9R | 18 | 35 |
| **B: meta-model** | **54** | **42.6%** | **1.21** | **+0.122** | **+6.6R** | **42** | **12** |
| C: meta>0.40 | 54 | 40.7% | 1.10 | +0.057 | +3.1R | 43 | 11 |
| always_long | 61 | 36.1% | 0.92 | -0.054 | -3.3R | 61 | 0 |
| always_short | 64 | 35.9% | 0.90 | -0.063 | -4.0R | 0 | 64 |

### Walk-forward (ข้อควรระวัง)

Walk-forward (ตอนเทรน) meta-model ติดลบทุก experiment เพราะ:
- fold น้อย (10 fold) → meta-model มี OOF data ไม่พอในช่วงแรก
- nested meta-model ต้องเห็นหลาย regime ถึงจะเรียนรู้ได้ดี
- holdout ใช้ OOF ทั้งหมดเทรน → ดีกว่ามาก

| Experiment | Trades | Win% | PF | Exp_R | Total_R |
|-----------|--------|------|----|-------|---------|
| A: tau/m | 126 | 50.0% | 1.20 | +0.087 | +11.0R |
| B: meta (no filter) | 794 | 39.8% | 0.91 | -0.052 | -41.6R |
| C: meta (conf>0.40) | 758 | 39.8% | 0.92 | -0.045 | -33.8R |
| D: meta (conf>0.50) | 556 | 41.5% | 0.97 | -0.020 | -10.9R |

### Meta-model Feature Importance (Gain)

| Feature | Importance % | Category |
|---------|-------------|----------|
| d1_adx14 | 11.1% | Daily trend strength |
| atr_regime | 9.4% | Volatility regime |
| regime_sma50_slope | 9.1% | Regime momentum |
| p_sum | 8.4% | Specialist total confidence |
| p_bear | 8.3% | Specialist bear prob |
| d1_rsi14 | 8.0% | Daily RSI |
| dxy_dist_sma20 | 8.0% | Dollar index |
| vix_level | 7.4% | VIX relative to SMA |
| regime_trend_str | 6.7% | Price vs SMA50 in ATR |
| p_diff | 6.6% | Bull-bear spread |
| adx14 | 6.5% | Intraday trend strength |
| p_bull | 4.6% | Specialist bull prob |
| p_max | 3.0% | Max specialist prob |
| session | 2.6% | Trading session |
| regime | 0.1% | Regime bucket |
| d1_trend | 0.0% | Daily trend direction |

**Key insight:** Meta-model ให้ความสำคัญกับ context (ADX, regime, VIX, DXY) มากกว่า specialist probs → ยืนยันว่ามันเรียนรู้ "เมื่อไหร่ควรเทรด" จากสภาพตลาด ไม่ใช่แค่ดูความน่าจะเป็นของ specialist

### Level-1 Specialist OOF Metrics (Intraday)

| Metric | Value |
|--------|-------|
| AUC bull | 0.549 |
| AUC bear | 0.603 |
| Bull scale_pos_weight | 2.05 (pos_rate 32.8%) |
| Bear scale_pos_weight | 4.70 (pos_rate 17.5%) |

### Meta Label Distribution (OOF)

| Class | Count | Share |
|-------|-------|-------|
| FLAT (0) | 8,273 | 50.4% |
| LONG (1) | 5,208 | 31.7% |
| SHORT (2) | 2,941 | 17.9% |

### Training Regime Distribution (2025-01 to 2025-07)

| Regime | Count |
|--------|-------|
| UP (+1) | 11,747 (87%) |
| SIDE (0) | 1,826 (13%) |
| DOWN (-1) | 0 (0%) |

---

## ทดลอง ML-1: LLM veto gate บน holdout Aug-Sep 2026 (2026-10-05)

Script: `notebook/llm_filter_experiment.py` (ผลใน `results/llm_filter/`, bars cache ใน `data/holdout_cache/`)
LLM: gpt-oss 120B ผ่าน Ollama Cloud, temperature 0, JSON output. LLM **เปลี่ยนทิศ/SL/TP ไม่ได้** ตอบได้แค่ take / half / skip
ต่อไม้ที่ meta-model C เสนอ โดยเห็น feature เดียวกับโมเดล (หน่วย ATR/ratio) + session + ชั่วโมงถึง/หลัง NFP·FOMC·CPI
- `anon` = ไม่เห็นวันที่และราคา (กัน LLM จำตลาดปี 2026 ได้) · `dated` = เห็นวันที่+ราคา (leakage probe)

| Arm | Trades | Win% | PF | Exp_R | Total_R | Max DD |
|-----|--------|------|----|-------|---------|--------|
| ML only (C) | 199 | 45.2% | 1.15 | +0.078 | +15.6R | 13.2% |
| + calendar rule (งด ≤2h ก่อน / ≤1h หลังข่าว) | 195 | 45.6% | 1.17 | +0.090 | +17.5R | 11.9% |
| + LLM binary veto, anon | 194 | 45.9% | 1.20 | +0.106 | +20.6R | 11.6% |
| + LLM veto half=0.5R, anon | 194 | 45.9% | 1.25 | +0.083 | +16.0R | 7.8% |
| + LLM binary veto, dated | 198 | 44.9% | 1.13 | +0.073 | +14.4R | 14.4% |
| + LLM veto half=0.5R, dated | 198 | 44.9% | 1.13 | +0.049 | +9.7R | 10.9% |

**คำตัดสินของ LLM บน 199 ไม้เดียวกันของ ML only (ผลจริงของแต่ละกลุ่ม):**

| โหมด | take | half | skip |
|------|------|------|------|
| anon | n=60, win 48.3%, +0.21R/ไม้ | n=127, win 44.1%, +0.03R | n=12, win 41.7%, −0.03R |
| dated | n=76, win 44.7%, +0.05R | n=110, win 46.4%, +0.11R | n=13, win 38.5%, +0.01R |

**สรุป:**
- Win rate เปลี่ยน <1 จุด ทุก arm (SE ของ win rate ที่ n=199 คือ ±3.5 จุด) → **ไม่มีนัยสำคัญ** ทั้งขึ้นและลง
- LLM แทบไม่ skip (12–15 ไม้) แต่ halve 64% ของไม้ทั้งที่ prompt บอกให้ take เป็นค่าเริ่มต้น; ไม้ที่ skip มีผลรวม ≈ 0R → การ veto ไม่ได้ตัด loser ออก
- Total R ของ anon binary ที่ดูดีขึ้น (+5R) มาจาก "ไม้ทดแทน" ที่ถูกเปิดหลัง skip (16 ไม้, +0.3R) และลำดับไม้ที่เปลี่ยน ไม่ใช่จากความแม่นของ veto
- ลำดับ take > half > skip ในโหมด anon ดูน่าสนใจ แต่ permutation test take vs half: Δwin +4.2pp p=0.64, ΔR +0.18 p=0.22 และ **โหมด dated ลำดับกลับด้าน** (half ดีกว่า take) → เป็น noise
- การเห็นวันที่/ราคา (dated) ไม่ทำให้ดีขึ้น → ไม่เห็นหลักฐาน leakage ในโมเดลนี้ แต่ก็ไม่เห็นประโยชน์
- calendar rule แบบ deterministic ให้ผลพอๆ กับ LLM โดยไม่มีต้นทุน/ความไม่แน่นอน
- **Random-veto control** (`llm_filter_random_control.py`, สุ่ม skip ด้วยอัตราเดียวกับ LLM 500 รอบ): PF 5–95% = [1.08, 1.20], Total R = [9.0, 20.2] → gpt-oss anon (PF 1.20, +20.6R) อยู่ที่ percentile 96 แต่ gpt-oss dated (PF 1.13) อยู่ที่ ~45 และ bootstrap PF ของ ML เองกว้าง [0.89, 1.47]
- **Replication ด้วย gemma4:31b (anon):** skip 134/330 (40%), PF 1.16, win 45.1%, +15.0R, DD แย่ลง; ไม้ที่ gemma4 skip มี win 54% +8.6R = **ตัดไม้ที่ดีที่สุดทิ้ง** → ผลบวกของ gpt-oss anon ไม่ซ้ำกับโมเดลอื่นและไม่ซ้ำกับตัวเองในโหมด dated
- ข้อสรุป: **LLM veto ไม่ช่วย win rate ของ v0.5** (ผลบวก 1 ครั้งที่ p≈0.04 จาก 4 arm × 3 metric ที่ดู แล้ว replicate ไม่ผ่าน 2 ครั้ง = noise) ค่าเดียวที่ดีขึ้นจริงคือ Max DD ของ arm half=0.5R ซึ่งเป็นผลจาก size เฉลี่ย 0.66 ไม่ใช่จากการเลือกไม้ → ถ้าต้องการ DD ต่ำลง ลด risk ต่อไม้ตรงๆ ง่ายกว่า

## ทดลอง ML-1b: LLM veto บน walk-forward OOF 2025-06..2026-01 (2026-10-05)

Script: `notebook/llm_filter_walkforward.py` — สัญญาณ nested walk-forward ของ exp C (out-of-fold, ML ไม่ in-sample),
gpt-oss 120B anon, prompt/simulator เดียวกับ holdout. 758 ไม้ (4 เท่าของ holdout)

| Arm | Trades | Win% | PF | Exp_R | Total_R | Max DD |
|-----|--------|------|----|-------|---------|--------|
| ML only (C) | 758 | 39.8% | 0.93 | −0.045 | −33.8R | 48.2% |
| + calendar rule | 738 | 40.7% | 0.96 | −0.024 | −17.8R | 40.8% |
| + LLM binary veto | 730 | 40.0% | 0.97 | −0.020 | −14.9R | 39.4% |
| + LLM veto half=0.5R | 730 | 40.0% | 1.00 | 0.000 | +0.1R | 24.3% |

- Random-veto control 300 รอบ: LLM อยู่ percentile 98–99 ทั้ง PF และ Total R (ต่างจาก holdout ที่ก้ำกึ่ง)
- คำตัดสินบน 758 ไม้เดียวกัน: take n=255 win 46.7% +0.055R · half n=395 win 37.0% −0.075R · skip n=108 win 34.3% −0.168R
  permutation take vs rest: Δwin +10.3pp **p=0.009**, ΔmeanR +0.15 p=0.12
- ทิศทางเดียวกับ holdout anon (take 48% > half 44% > skip 42%) → gpt-oss anon สอดคล้องกัน 2 ช่วง; gemma4 และโหมด dated ไม่สอดคล้อง
- **ส่วนใหญ่คือกฎ "ไม่สวนเทรนด์รายวัน"**: LLM take ไม้สวนเทรนด์แค่ 6% / skip 33%. กฎ deterministic "skip counter-trend"
  ลดขาดทุนจาก −33.8R → −7.5R ได้เอง (meanR −0.016); LLM take-only ได้ +0.055R
- ในไม้ที่ตามเทรนด์อยู่แล้ว LLM ยังแยกได้: take win 47.5% +0.07R vs ที่เหลือ 39% −0.10R (p=0.08) → มีส่วนเพิ่มเหนือกฎ แต่ยังไม่ significant
- ข้อควรระวัง: win rate ของไม้สวนเทรนด์ต่ำโดยกลไก (TP 2.5R vs 1.5R) ให้ดู meanR เป็นหลัก; รายเดือนไม่สม่ำเสมอ (ต.ค. take แย่กว่า);
  LLM อาจจำได้ว่าปี 2025 ทองขึ้นทั้งปี
- **ข้อค้นพบสำหรับ ML:** meta-model ให้ importance `d1_trend` = 0% แต่การสวนเทรนด์คือตัวแยกไม้แพ้ที่ใหญ่ที่สุด → ควรใส่ trend filter/feature
  ให้ ML เองใน v0.6 แทนการพึ่ง LLM
- forward A/B เพิ่ม arm `trend_rule` แล้ว เพื่อแยกส่วนที่ LLM เพิ่มจริงเหนือกฎ

## ทดลอง v0.6 candidate: trend filter (กฎ "skip ไม้สวนเทรนด์รายวัน") บน v0.5 (2026-10-05)

Script: `notebook/llm_v6_trend.py` — กฎ deterministic บนสัญญาณ ML (ไม่เทรนใหม่: training เป็น UP 87% โมเดลไม่เคยเห็นขาลง
เรียนรู้ d1_trend ไม่ได้) กฎถูกเลือกหลังเห็นผล walk-forward → in-sample ของ "กฎ" ส่วน holdout ไม่ได้ใช้เลือกกฎ แต่เคยถูกดูมาแล้วหลายรอบ

| Arm | WF trades | WF Win% | WF PF | WF Total R | WF DD | HO trades | HO Win% | HO PF | HO Total R | HO DD |
|-----|-----------|---------|-------|------------|-------|-----------|---------|-------|------------|-------|
| ML only | 758 | 39.8 | 0.93 | −33.8 | 48.2% | 199 | 45.2 | 1.15 | +15.6 | 13.2% |
| trend rule | 509 | 43.6 | 0.98 | −5.1 | 22.9% | 140 | 46.4 | 1.12 | +9.1 | 10.5% |
| LLM binary | 730 | 40.0 | 0.97 | −14.9 | 39.4% | 194 | 45.9 | 1.20 | +20.6 | 11.6% |
| trend + LLM binary | 507 | 44.0 | 1.01 | +1.3 | 19.2% | 138 | 47.1 | 1.15 | +11.1 | 10.5% |
| trend + LLM half=0.5R | 507 | 44.0 | 1.04 | +8.2 | 13.2% | 138 | 47.1 | 1.26 | +12.0 | 4.7% |

- **กฎ trend ไม่ผ่านการยืนยันนอกตัวอย่าง:** ใน WF ไม้สวนเทรนด์เสีย −26.3R (meanR −0.095) แต่ใน holdout ไม้สวนเทรนด์ **กำไร** +3.4R (meanR +0.052)
  กฎจึงตัดกำไรทิ้ง 6.5R และ PF ลด 1.15→1.12 → เป็นการ fit กับ regime ปี 2025 (ทองขึ้นต่อเนื่อง) ได้พอๆ กับเป็นความจริงถาวร; อย่าใส่ใน backend จนกว่า forward จะยืนยัน
- **LLM เหนือกฎ (ไม้ที่ตามเทรนด์):** take vs rest — WF Δwin +7.6pp p=0.11, ΔmeanR +0.16 p=0.14; holdout Δwin +10.3pp p=0.36, ΔmeanR +0.29 p=0.18
  ทิศทางตรงกันทั้ง 2 ช่วง แต่ไม่มีช่วงไหน significant และรวมกันก็ยัง p≈0.12–0.17 (Fisher)
- trend + LLM half เป็น arm เดียวที่ PF ≥ 1 ทั้ง 2 ช่วง และ DD ต่ำสุด แต่ส่วนหนึ่งคือ size เฉลี่ยลดลงครึ่ง ไม่ใช่การเลือกไม้
- ข้อสรุป: edge ของ v0.5 ยังบางมาก (WF PF ≤ 1.04 ในทุก arm) ไม่มีกฎหรือ LLM ตัวไหนเปลี่ยนภาพนี้อย่างน่าเชื่อถือ → ตัดสินที่ forward A/B (มี arm `trend_rule`)

## ตารางตัดสิน: อันไหนพอใช้จริง (2026-10-05) — `notebook/llm_v6_decision.py`

เกณฑ์ที่ตั้งก่อนดูตาราง: (1) R เฉลี่ย > 0 ทั้ง WF และ holdout (2) pooled P(R>0) ≥ 0.90 จาก bootstrap (3) กฎ/โมเดลไม่ได้ถูกเลือกจากช่วงที่ใช้วัด (4) max DD < 25% ทั้งสองช่วง

| Arm | WF R/ไม้ [CI90] | HO R/ไม้ [CI90] | pooled R [CI90] | pooled P(>0) | เกณฑ์ที่ผ่าน |
|-----|----------------|----------------|-----------------|--------------|-------------|
| ML only | −0.045 [−0.12,+0.03] | +0.078 [−0.06,+0.22] | −0.019 [−0.08,+0.05] | 0.31 | 3 |
| trend rule | −0.010 [−0.09,+0.07] | +0.065 [−0.09,+0.23] | +0.006 [−0.07,+0.09] | 0.57 | 4 |
| LLM binary | −0.020 [−0.10,+0.06] | +0.106 [−0.04,+0.25] | +0.006 [−0.06,+0.07] | 0.57 | 3 |
| trend + LLM binary | +0.003 [−0.08,+0.09] | +0.081 [−0.08,+0.24] | +0.019 [−0.06,+0.10] | 0.67 | 1,4 |
| trend + LLM half | +0.021 [−0.06,+0.11] | +0.128 [−0.04,+0.29] | +0.042 [−0.04,+0.12] | 0.81 | 1,4 |

**ไม่มี arm ไหนผ่านครบ** ช่วงความเชื่อมั่นทุกตัวคร่อมศูนย์; ตัวดีที่สุด (trend + LLM half) ได้ P(>0)=0.81 และถูกเลือกด้วยกฎที่ fit กับ WF
calendar rule (ไม่ได้ fit จากข้อมูล เป็นเหตุผลล่วงหน้า) ลดขาดทุนทั้งสองช่วง (WF −33.8→−17.8R, HO +15.6→+17.5R) แต่ WF ยังติดลบ

## Roadmap: ML Phase ถัดไป

### Phase ML-1: LLM Event Filter — ทดลองแล้ว (ดูด้านบน) ไม่ช่วย; เหลือเฉพาะเวอร์ชันที่ใช้ข่าวจริงแบบ forward test

**Forward A/B เริ่มแล้ว 2026-10-05 00:00 server** — `notebook/forward_ab.py` (รันรายชั่วโมง, log ใน `results/forward_ab/`):
ML only vs calendar rule vs LLM veto (gpt-oss 120B, anon) บนแท่งสด LLM ตอบตอนแท่งปิด (`decided_at` ใน `candidates.jsonl`)
ให้ผลคะแนนเฉพาะไม้ที่ครบ 16 แท่งแล้ว ตัดสินเมื่อครบ ~100 ไม้ต่อ arm (`report.md`)

**ปัญหา:** Sep 2026 ขาดทุน -5.8R — event risk (FOMC, NFP, CPI) ทำให้ signal ผิดพลาด calendar features บอกแค่ "วันนี้เป็นวัน NFP" แต่ไม่รู้ consensus หรือ market expectation

**แผน:**
1. LLM อ่านข่าว real-time (ใช้ news data providers ที่มีอยู่แล้ว) ก่อนส่ง signal
2. ถ้ามี high-impact event ใน 2 ชม.: LLM ประเมินว่า signal ยังสอดคล้องกับ narrative ไหม
3. ถ้าขัดแย้งหรือไม่แน่ใจ → override เป็น FLAT
4. Format: `Meta-model signal → LLM event check → final signal`

**ประเมิน ROI:**
- Calendar features อย่างเดียว (v0.3) ไม่ช่วย (AUC เพิ่มน้อยมาก)
- LLM อ่าน context ได้ลึกกว่า (consensus vs actual, forward guidance, geopolitics)
- เป็น filter ไม่ใช่ generator — ลด risk มากกว่าเพิ่ม alpha

**สิ่งที่ต้องทำ:**
- [ ] เพิ่ม tool `check_event_risk(symbol, horizon, signal)` ใน trading workflow
- [ ] LLM prompt: "Given this ML signal and upcoming events, should we trade?"
- [ ] Backtest: เปรียบเทียบ "meta-model alone" vs "meta-model + LLM filter" บน holdout
- [ ] ถ้า LLM filter ช่วย: integrate เข้า workflow ก่อน policy gate

### Phase ML-2: More Training Data / Regime Diversity

**ปัญหา:** Training data 2025-01..2026-01 มี UP regime 87%, DOWN 0% → meta-model ไม่เคยเห็น DOWN regime ตอนเทรน

**แผน:**
- เพิ่มข้อมูลย้อนหลังถึง 2024 (v0.3 ทำแล้วแต่ v0.5 ยังใช้ 2025-01)
- หรือใช้ synthetic DOWN regime จาก data augmentation
- Re-train meta-model ด้วย regime diversity ที่มากขึ้น

### Phase ML-3: Online Learning / Model Refresh

**ปัญหา:** Model ถูก train ครั้งเดียวแล้วใช้ไปเรื่อย — regime เปลี่ยนไปเรื่อยๆ

**แผน:**
- Periodic re-training (monthly) ด้วยข้อมูลใหม่
- Monitor model degradation: ถ้า holdout PF < 1.0 ติดต่อกัน 2 เดือน → alert
- Automated pipeline: notebook → export → backend (CI/CD)
