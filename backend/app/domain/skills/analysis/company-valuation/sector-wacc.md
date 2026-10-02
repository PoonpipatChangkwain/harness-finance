# Sector WACC Adjustments

Ported from Dexter's `dcf/sector-wacc.md`. Use these typical WACC ranges as starting points, then adjust for company-specific factors. Get the sector from `get_company_profile`.

## WACC by sector (USD-based, developed market)

| Sector | Typical WACC | Notes |
|--------|--------------|-------|
| Communication Services | 8-10% | Mix of stable telecom and growth media |
| Consumer Discretionary | 8-10% | Cyclical exposure |
| Consumer Staples | 7-8% | Defensive, stable demand |
| Energy | 9-11% | Commodity price exposure |
| Financials | 8-10% | Leverage already in the business model |
| Health Care | 8-10% | Regulatory and pipeline risk |
| Industrials | 8-9% | Moderate cyclicality |
| Information Technology | 8-12% | Higher for high-growth |
| Materials | 8-10% | Cyclical, commodity exposure |
| Real Estate | 7-9% | Interest-rate sensitivity |
| Utilities | 6-7% | Regulated, stable cash flows |

## Adjustment factors

Add to base WACC:
- High debt (D/E > 1.5): +1-2%
- Small cap (< $2B market cap): +1-2%
- Emerging-market exposure (includes SET-listed companies valued in THB): +1-3%
- Concentrated customer base: +0.5-1%
- Regulatory uncertainty (e.g. Thai energy price controls): +0.5-1.5%

Subtract from base WACC:
- Market leader with a moat: -0.5-1%
- Recurring revenue model: -0.5-1%
- Investment-grade credit rating: -0.5%

## Reasonableness checks

- WACC is typically 2-4% below ROIC for value-creating companies
- WACC above ROIC suggests the company may be destroying value
- Compare with sector peers when available
