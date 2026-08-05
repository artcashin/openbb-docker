"""Fetching a cache miss from whichever provider the user configured.

Providers are resolved BY NAME through OpenBB's registry rather than imported,
which is what lets KDB_UPSTREAM be any installed provider instead of hardwiring
EODHD.
"""

from typing import Any

_REGISTRY_CACHE: dict[str, Any] | None = None


class UpstreamError(Exception):
    """The configured upstream provider cannot serve this request."""


def _load_registry() -> dict[str, Any]:
    """Map provider name -> Provider object."""
    from openbb_core.provider.registry import RegistryLoader

    return RegistryLoader.from_extensions().providers


def _registry() -> dict[str, Any]:
    global _REGISTRY_CACHE  # noqa: PLW0603
    if _REGISTRY_CACHE is None:
        _REGISTRY_CACHE = _load_registry()
    return _REGISTRY_CACHE


async def fetch_gap(
    provider: str, model: str, params: dict, credentials: dict | None
) -> list[dict]:
    """Fetch one missing range from the upstream provider."""
    if provider.lower() == "kdb":
        raise UpstreamError(
            "kdb cannot be its own upstream (KDB_UPSTREAM must not point back to itself)."
        )
    registry = _registry()
    prov = registry.get(provider)
    if prov is None:
        raise UpstreamError(
            f"Upstream provider {provider!r} is not installed. Available: "
            f"{sorted(registry)}"
        )
    fetcher = prov.fetcher_dict.get(model)
    if fetcher is None:
        raise UpstreamError(f"Provider {provider!r} does not implement {model}.")
    result = await fetcher.fetch_data(params, credentials)
    rows = getattr(result, "result", result)
    return [r if isinstance(r, dict) else r.model_dump() for r in rows]
