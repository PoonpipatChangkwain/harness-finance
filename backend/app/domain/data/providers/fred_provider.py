"""FRED (Federal Reserve Economic Data) — macro series, free API key.

Ported from ``../TradingAgents/tradingagents/dataflows/vendors/fred.py``:
curated aliases resolve to FRED series IDs (raw IDs pass through
unchanged), and the realtime vintage is clamped to FRED's own clock
(America/Chicago) since FRED rejects a ``realtime_start`` in its future
with a 400.
"""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from app.domain.data.base import BadRequest, Capability, DataRequest, DataResult, NoData, NotConfigured
from app.domain.data.http import make_client, request_with_retry
from app.domain.data.schemas import MacroObservation, MacroSeries
from app.domain.models.provenance import new_record
from app.domain.models.symbol import Symbol
from app.settings import get_settings

_BASE = "https://api.stlouisfed.org/fred"
_TZ = ZoneInfo("America/Chicago")

MACRO_SERIES: dict[str, str] = {
    "fed_funds_rate": "FEDFUNDS",
    "federal_funds_rate": "FEDFUNDS",
    "fed_funds": "FEDFUNDS",
    "2y_treasury": "DGS2",
    "10y_treasury": "DGS10",
    "30y_treasury": "DGS30",
    "10y_2y_spread": "T10Y2Y",
    "yield_curve": "T10Y2Y",
    "cpi": "CPIAUCSL",
    "core_cpi": "CPILFESL",
    "pce": "PCEPI",
    "core_pce": "PCEPILFE",
    "real_gdp": "GDPC1",
    "gdp": "GDP",
    "unemployment_rate": "UNRATE",
    "unemployment": "UNRATE",
    "nonfarm_payrolls": "PAYEMS",
    "payrolls": "PAYEMS",
    "vix": "VIXCLS",
    "dollar_index": "DTWEXBGS",
}


def _resolve_series_id(indicator: str) -> str:
    key = indicator.strip().lower().replace(" ", "_").replace("-", "_")
    if key in MACRO_SERIES:
        return MACRO_SERIES[key]
    candidate = indicator.strip().upper()
    if not candidate or len(candidate) > 30 or any(c.isspace() for c in candidate):
        raise BadRequest(
            f"{indicator!r} is not a known macro alias or a valid FRED series ID "
            f"(e.g. 'cpi', '10y_treasury', or a raw series ID like 'CPIAUCSL')"
        )
    return candidate


def _now_in_chicago() -> str:
    from datetime import datetime

    return datetime.now(_TZ).strftime("%Y-%m-%d")


class FredProvider:
    id = "fred"
    capabilities = frozenset({Capability.MACRO})

    def is_configured(self) -> bool:
        return bool(get_settings().fred_api_key)

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        api_key = get_settings().fred_api_key
        if not api_key:
            raise NotConfigured("FRED_API_KEY is not set")

        series_id = _resolve_series_id(str(req.params.get("series_id", req.params.get("series", ""))))
        realtime = min(_now_in_chicago(), (req.as_of or date.today()).isoformat())
        params = {
            "series_id": series_id,
            "api_key": api_key.get_secret_value(),
            "file_type": "json",
            "realtime_start": realtime,
            "realtime_end": realtime,
            "observation_start": str(req.params.get("start", "")) or "1900-01-01",
            "observation_end": str(req.params.get("end", "")) or realtime,
        }

        async with make_client() as client:
            resp = await request_with_retry(client, "GET", f"{_BASE}/series/observations", params=params)

        body = resp.json()
        if resp.status_code == 400:
            raise BadRequest(body.get("error_message", "FRED bad request"))
        raw_obs = body.get("observations", [])
        if not raw_obs:
            raise NoData(f"no observations for {series_id}")

        observations = [
            MacroObservation(
                date=date.fromisoformat(o["date"]), value=None if o["value"] == "." else float(o["value"])
            )
            for o in raw_obs
        ]
        values = [o.value for o in observations if o.value is not None]
        pct_change = (values[-1] / values[-2] - 1.0) if len(values) >= 2 and values[-2] != 0 else None

        series = MacroSeries(
            series_id=series_id, title=series_id, units="", observations=observations, pct_change=pct_change
        )
        record = new_record(
            "data",
            series_id,
            source=self.id,
            tool="macro_series",
            params={"series_id": series_id},
            as_of=req.as_of,
            url=f"https://fred.stlouisfed.org/series/{series_id}",
        )
        return DataResult(
            provider=self.id, capability=Capability.MACRO, data=series, records=[record], status="ok"
        )
