"""EODHD world news (/news).

Without a topic the endpoint serves the general financial feed; `topic`
narrows it by EODHD's dynamic tag set. Symbol-scoped news (CompanyNews) is a
Phase-2 feature and not part of this module.
"""

from typing import Any

from openbb_core.provider.abstract.fetcher import Fetcher
from openbb_core.provider.standard_models.world_news import (
    WorldNewsData, WorldNewsQueryParams,
)
from openbb_core.provider.utils.errors import EmptyDataError, UnauthorizedError
from pydantic import Field

from openbb_eodhd.models._client import rest_json, sdk_call


class EODHDWorldNewsQueryParams(WorldNewsQueryParams):
    """EODHD World News Query."""

    topic: str | None = Field(
        default=None,
        description="EODHD topic tag, e.g. 'technology', 'mergers and acquisitions'."
        " Omitted, the general financial feed is served.",
    )


class EODHDWorldNewsData(WorldNewsData):
    """EODHD World News Data."""

    symbols: list[str] | None = Field(default=None, description="Tickers the article mentions.")
    tags: list[str] | None = Field(default=None, description="EODHD topic tags on the article.")
    sentiment: dict | None = Field(default=None, description="EODHD polarity/pos/neu/neg scores.")


class EODHDWorldNewsFetcher(
    Fetcher[EODHDWorldNewsQueryParams, list[EODHDWorldNewsData]]
):
    """EODHD world news."""

    @staticmethod
    def transform_query(params: dict[str, Any]) -> EODHDWorldNewsQueryParams:
        return EODHDWorldNewsQueryParams(**params)

    @staticmethod
    async def aextract_data(query, credentials, **kwargs) -> list[dict]:  # pylint: disable=unused-argument
        from asyncio import to_thread

        def _sync():
            # The SDK's financial_news wrapper insists on s or t, but the API
            # itself serves a general feed with neither — go through the raw
            # /news endpoint so an empty topic stays valid.
            resp = sdk_call(
                credentials,
                lambda c: rest_json(c, "news", {
                    "t": query.topic,
                    "from": str(query.start_date) if query.start_date else None,
                    "to": str(query.end_date) if query.end_date else None,
                    "limit": query.limit,
                }),
                "world news",
            )
            if isinstance(resp, dict):
                raise UnauthorizedError(
                    f"EODHD (world news): {resp.get('message') or resp.get('error') or resp}"
                )
            if not resp:
                raise EmptyDataError("EODHD returned no news.")
            return resp

        return await to_thread(_sync)

    @staticmethod
    def transform_data(query, data: list[dict], **kwargs) -> list[EODHDWorldNewsData]:  # pylint: disable=unused-argument
        rows = []
        for it in data:
            if not it.get("date") or not it.get("title"):
                continue
            content = it.get("content") or ""
            rows.append(EODHDWorldNewsData.model_validate({
                "date": it["date"],
                "title": it["title"],
                "body": content or None,
                "excerpt": (content[:280] or None) if content else None,
                "url": it.get("link"),
                "symbols": it.get("symbols") or None,
                "tags": it.get("tags") or None,
                "sentiment": it.get("sentiment") or None,
            }))
        return rows
