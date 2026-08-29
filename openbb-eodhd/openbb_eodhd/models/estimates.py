"""EODHD earnings/estimates models from /fundamentals (History + Trend)."""

from typing import Any

from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.historical_eps import (
    HistoricalEpsData, HistoricalEpsQueryParams,
)
from pydantic import Field

from openbb_eodhd.models import _fundamentals as F


def _date(v):
    from pandas import isna, to_datetime
    if not v:
        return None
    ts = to_datetime(v, errors="coerce")
    return None if isna(ts) else ts.date()


def _f(v):
    try:
        return float(v) if v not in (None, "", "NA") else None
    except (TypeError, ValueError):
        return None


class EODHDHistoricalEpsQueryParams(HistoricalEpsQueryParams):
    """EODHD Historical EPS Query."""
    exchange: str = Field(default="US", description="EODHD exchange code for bare symbols.")


class EODHDHistoricalEpsData(HistoricalEpsData):
    """EODHD Historical EPS Data."""
    report_date: Any = Field(default=None, description="Announced report date.")


class EODHDHistoricalEpsFetcher(
    Fetcher[EODHDHistoricalEpsQueryParams, list[EODHDHistoricalEpsData]]
):
    """EODHD historical EPS (Earnings.History)."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDHistoricalEpsQueryParams:
        return EODHDHistoricalEpsQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> dict:  # pylint: disable=unused-argument
        return await F.get_bundle(query.symbol, query.exchange, credentials)

    @staticmethod
    def transform_data(query, data: dict, **kwargs) -> list[EODHDHistoricalEpsData]:  # pylint: disable=unused-argument
        rows = []
        for e in F.earnings_history(data):
            d = _date(e.get("date") or e.get("reportDate"))
            if d is None:
                continue
            rows.append(EODHDHistoricalEpsData.model_validate({
                "symbol": query.symbol.upper(),
                "date": d,
                "eps_actual": _f(e.get("epsActual")),
                "eps_estimated": _f(e.get("epsEstimate")),
                "report_date": _date(e.get("reportDate")),
            }))
        rows.sort(key=lambda r: r.date)
        return rows
