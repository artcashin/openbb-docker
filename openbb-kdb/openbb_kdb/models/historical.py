"""OpenBB fetchers backed by the read-through cache.

One shared session per process: the q child process and its connection are
created once and reused by every fetcher.
"""

from datetime import date as dateType, datetime
from typing import Any

from openbb_core.provider.abstract.annotated_result import AnnotatedResult
from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.crypto_historical import (
    CryptoHistoricalData,
    CryptoHistoricalQueryParams,
)
from openbb_core.provider.standard_models.currency_historical import (
    CurrencyHistoricalData,
    CurrencyHistoricalQueryParams,
)
from openbb_core.provider.standard_models.equity_historical import (
    EquityHistoricalData,
    EquityHistoricalQueryParams,
)
from openbb_core.provider.standard_models.etf_historical import (
    EtfHistoricalData,
    EtfHistoricalQueryParams,
)
from openbb_core.provider.standard_models.index_historical import (
    IndexHistoricalData,
    IndexHistoricalQueryParams,
)

_SESSION = None
_CACHE = None


def _cache(credentials: dict | None):
    """Build (once) the shared session, store and cache."""
    global _SESSION, _CACHE  # noqa: PLW0603
    from openbb_kdb.cache import ReadThroughCache
    from openbb_kdb.config import resolve_config
    from openbb_kdb.session import KdbSession
    from openbb_kdb.store import KdbStore

    if _CACHE is None:
        config = resolve_config(credentials)
        _SESSION = KdbSession(config)
        _CACHE = ReadThroughCache(KdbStore(_SESSION), config)
    return _CACHE


def _default_window(params: dict) -> dict:
    """Default to one year -- the window the demo chart opens on."""
    from dateutil.relativedelta import relativedelta

    out = dict(params)
    today = datetime.now().date()
    if out.get("start_date") is None:
        out["start_date"] = today - relativedelta(years=1)
    if out.get("end_date") is None:
        out["end_date"] = today
    return out


def _as_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, dateType):
        return datetime(value.year, value.month, value.day)
    return datetime.fromisoformat(str(value))


async def _extract(query, credentials: dict | None, model: str) -> dict:
    """Run the read-through cache and hand back rows plus telemetry."""
    cache = _cache(credentials)
    params = query.model_dump(exclude_none=True)
    interval = getattr(query, "interval", None) or "1d"
    rows, meta = await cache.get(
        symbol=query.symbol,
        interval=interval,
        start=_as_datetime(query.start_date),
        end=_as_datetime(query.end_date),
        model=model,
        params=params,
        credentials=credentials,
    )
    return {"rows": rows, "meta": meta}


def _annotate(data: dict, data_cls) -> AnnotatedResult:
    results = [data_cls.model_validate(r) for r in data["rows"]]
    return AnnotatedResult(result=results, metadata=data["meta"])


class KdbEquityHistoricalFetcher(
    Fetcher[EquityHistoricalQueryParams, list[EquityHistoricalData]]
):
    """Equity bars served from the kdb+ cache, filled from the upstream provider."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EquityHistoricalQueryParams:
        return EquityHistoricalQueryParams(**_default_window(params))

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> dict:
        return await _extract(query, credentials, "EquityHistorical")

    @staticmethod
    def transform_data(query, data: dict, **kwargs) -> AnnotatedResult:
        return _annotate(data, EquityHistoricalData)


class KdbEtfHistoricalFetcher(Fetcher[EtfHistoricalQueryParams, list[EtfHistoricalData]]):
    """ETF bars served from the kdb+ cache."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EtfHistoricalQueryParams:
        return EtfHistoricalQueryParams(**_default_window(params))

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> dict:
        return await _extract(query, credentials, "EtfHistorical")

    @staticmethod
    def transform_data(query, data: dict, **kwargs) -> AnnotatedResult:
        return _annotate(data, EtfHistoricalData)


class KdbCryptoHistoricalFetcher(
    Fetcher[CryptoHistoricalQueryParams, list[CryptoHistoricalData]]
):
    """Crypto bars served from the kdb+ cache."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> CryptoHistoricalQueryParams:
        return CryptoHistoricalQueryParams(**_default_window(params))

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> dict:
        return await _extract(query, credentials, "CryptoHistorical")

    @staticmethod
    def transform_data(query, data: dict, **kwargs) -> AnnotatedResult:
        return _annotate(data, CryptoHistoricalData)


class KdbCurrencyHistoricalFetcher(
    Fetcher[CurrencyHistoricalQueryParams, list[CurrencyHistoricalData]]
):
    """FX bars served from the kdb+ cache."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> CurrencyHistoricalQueryParams:
        return CurrencyHistoricalQueryParams(**_default_window(params))

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> dict:
        return await _extract(query, credentials, "CurrencyHistorical")

    @staticmethod
    def transform_data(query, data: dict, **kwargs) -> AnnotatedResult:
        return _annotate(data, CurrencyHistoricalData)


class KdbIndexHistoricalFetcher(
    Fetcher[IndexHistoricalQueryParams, list[IndexHistoricalData]]
):
    """Index bars served from the kdb+ cache."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> IndexHistoricalQueryParams:
        return IndexHistoricalQueryParams(**_default_window(params))

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> dict:
        return await _extract(query, credentials, "IndexHistorical")

    @staticmethod
    def transform_data(query, data: dict, **kwargs) -> AnnotatedResult:
        return _annotate(data, IndexHistoricalData)
