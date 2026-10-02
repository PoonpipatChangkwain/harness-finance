"""sec-api.io Extractor API — a specific 10-K/10-Q section as text, via REST.

Ported concept from ``../FinRobot/finrobot/data_source/sec_utils.py``, but
called directly over REST (``GET /extractor``) instead of the ``sec_api``
SDK, per data-sources.md §5. Optional: SEC EDGAR already covers most
statement/filing needs; this exists for pulling a specific narrative
section (e.g. Item 1A "Risk Factors") as clean text.
"""

from __future__ import annotations

from app.domain.data.base import BadRequest, Capability, DataRequest, DataResult, NoData, NotConfigured
from app.domain.data.http import make_client, request_with_retry
from app.domain.models.provenance import new_record
from app.domain.models.symbol import Symbol
from app.settings import get_settings

_BASE = "https://api.sec-api.io/extractor"


class SecApiProvider:
    id = "sec_api"
    capabilities = frozenset({Capability.FILINGS})

    def is_configured(self) -> bool:
        return bool(get_settings().sec_api_key)

    def supports(self, cap: Capability, symbol: Symbol | None) -> bool:
        return cap in self.capabilities

    def _token(self) -> str:
        key = get_settings().sec_api_key
        if not key:
            raise NotConfigured("SEC_API_KEY is not set")
        return key.get_secret_value()

    async def fetch(self, cap: Capability, req: DataRequest) -> DataResult:
        filing_url = str(req.params.get("filing_url", ""))
        item = str(req.params.get("item", "1A"))
        if not filing_url:
            raise BadRequest("sec_api FILINGS requires params.filing_url")

        params = {"url": filing_url, "item": item, "type": "text", "token": self._token()}
        async with make_client() as client:
            resp = await request_with_retry(client, "GET", _BASE, params=params)
        text = resp.text
        if not text or "error" in text.lower()[:50]:
            raise NoData(f"no section {item} extracted from {filing_url}")

        record = new_record(
            "data",
            {"chars": len(text)},
            source=self.id,
            tool="extractor",
            params={"item": item},
            url=filing_url,
        )
        return DataResult(
            provider=self.id,
            capability=Capability.FILINGS,
            data={"item": item, "text": text, "url": filing_url},
            records=[record],
            status="ok",
        )
