---
name: company-valuation
description: Intrinsic and relative valuation — DCF fair value per share with sensitivity, plus peer multiples. Use when the user asks for fair value, intrinsic value, DCF, "หุ้นนี้ถูกหรือแพง", price target, undervalued/overvalued, or compares price to fundamental value.
category: analysis
role: valuation_specialist
inputs: [symbol]
tools: [get_statements, get_fundamentals, get_company_profile, get_quote, compute_dcf, compute_comps, compute_returns_risk]
---

# Company Valuation (DCF + comps)

Ported from Dexter's `dcf-valuation` skill. The difference: **you never do arithmetic**. You choose bounded assumptions; `compute_dcf` and `compute_comps` do every calculation (F1). Answer in the user's language.

## Checklist
```
- [ ] 1. Gather data
- [ ] 2. Choose growth assumptions
- [ ] 3. Choose WACC (sector table)
- [ ] 4. Run compute_dcf (base + built-in sensitivity)
- [ ] 5. Run compute_comps (relative check)
- [ ] 6. Validate
- [ ] 7. Present with caveats
```

## Step 1 — Gather data (same turn)
- `get_statements(period="annual")` — free cash flow, operating cash flow, capex, debt, cash, shares
- `get_fundamentals` — revenue/earnings growth, ROE, debt/equity, beta, EPS
- `get_company_profile` — sector and industry (for the WACC table)
- `get_quote` — current price

## Step 2 — Growth assumptions
Pick year-1 growth from reported growth (`revenueGrowth`, `earningsGrowth`) and the FCF trend you can see.
- Stable history → take the reported growth with a 10–20% haircut
- **Cap at 15%**; fade it each year (e.g. ×0.95, ×0.90, ×0.85, ×0.80 for years 2–5)
- Terminal growth 2.0–3.0% (GDP proxy; use ≤2.5% for Thai companies unless justified)

Write the growth list yourself — that is an assumption, not a calculation. `compute_dcf` records it as `llm_assumption`.

## Step 3 — WACC
Use the sector from `get_company_profile` and the ranges in [sector-wacc.md](sector-wacc.md). Adjust with the listed factors (debt, size, emerging-market exposure +1–3% for SET names). State the final WACC and why.

## Step 4 — DCF
Call `compute_dcf` with `assumptions = {wacc, terminal_growth, years, growth_rates}`.
- The tool pulls FCF, net debt and shares from the latest annual statements.
- If it reports missing inputs, find them with another tool and pass `fcf0` / `net_debt` / `shares` overrides (those are recorded as assumptions too).
- It returns per-share value, EV, terminal-value share of EV and a 3×3 WACC × terminal-growth sensitivity grid.

## Step 5 — Comps
Call `compute_comps` with 3–6 peers (same sector; use `list_sector_symbols` if you need names) and the multiple that fits the business (pe for most, ev_ebitda for capital-heavy, pb for banks).

## Step 6 — Validate
Using only tool outputs:
1. Terminal value share of EV should be 50–80% for mature companies (>90% → growth too high)
2. DCF value vs comps-implied range: explain large gaps instead of hiding them
3. WACC should be below ROE/ROIC for value-creating companies
If a check fails, adjust assumptions and call `compute_dcf` again (you may call it up to 3 times).

## Step 7 — Output
1. **Valuation summary**: current price vs DCF value per share and vs peers (numbers from tools)
2. **Assumptions table**: each assumption, value, and why (label them as assumptions)
3. **DCF result**: EV, equity value, per share, terminal-value share
4. **Sensitivity matrix** exactly as returned
5. **Peer comparison** from `compute_comps`
6. **Caveats**: DCF limits, data gaps, company-specific risks
