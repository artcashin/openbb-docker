"""yfinance as a reference source.

yfinance returns an EMPTY FRAME rather than raising when Yahoo refuses a
window, but Yahoo's own explanation ("The requested range must be within the
last 30 days") is worth surfacing verbatim -- it is the whole reason a 2023
tick sample cannot be checked minute-by-minute against this source. So
exceptions are switched on and the message is classified.

Only yfinance's OWN exception hierarchy (`yfinance.exceptions.YFException`,
confirmed empirically against yfinance 1.5.2 -- see its `exceptions.py`) is
caught and classified. A bug in our code (AttributeError, TypeError, a
mistyped attribute, ...) is not a data-availability problem and must
propagate unchanged rather than being misreported as "no interval could
serve this symbol".
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tick_lab.reference.base import ReferenceError
from tick_lab.rollup import BAR_COLUMNS

# Yahoo's retention ceilings, for reference: 1m ~30 days, 2m-90m ~60 days,
# 1h ~730 days, 1d unlimited.
_SUPPORTED = ("1m", "5m", "15m", "30m", "1h", "1d")

# Yahoo's column names -> ours. This mapping is yfinance-specific; the
# output column order and membership come from BAR_COLUMNS (the rollup
# module's declared interface), not from this dict.
_YAHOO_COLUMNS = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",
}


def classify(message: str) -> ReferenceError:
    """Turn a yfinance exception message into a classified ReferenceError.

    "must be within the last" is the one positively-recognised shape: Yahoo's
    retention-limit wording, which is steppable (try a coarser interval).
    Everything else we cannot positively identify -- a rate limit, a genuine
    entitlement rejection, an outage, an unfamiliar message -- defaults to
    `transport`, which `fetch_finest` will not step past. Stepping down the
    ladder is a privilege earned only by messages we recognise.
    """
    if "must be within the last" in message:
        return ReferenceError("retention", message)
    return ReferenceError("transport", message)


class YFinanceAdapter:
    name = "yfinance"
    supported_intervals = _SUPPORTED

    def _history(self, symbol: str, start: Any, end: Any, interval: str) -> pd.DataFrame:
        import yfinance as yf

        # Surface Yahoo's explanation instead of an empty frame. The old
        # `raise_errors=` argument is deprecated in favour of this.
        try:
            yf.config.debug.hide_exceptions = False
        except AttributeError:  # pragma: no cover - older yfinance
            pass
        return yf.Ticker(symbol).history(
            start=start, end=end, interval=interval, auto_adjust=False
        )

    def fetch(self, symbol: str, start: Any, end: Any, interval: str) -> pd.DataFrame:
        from yfinance.exceptions import YFException

        try:
            raw = self._history(symbol, start, end, interval)
        except YFException as err:
            raise classify(str(err)) from err

        if raw is None or raw.empty:
            raise ReferenceError(
                "empty", f"yfinance returned no rows for {symbol} at {interval}"
            )

        frame = raw.rename(columns=_YAHOO_COLUMNS)[BAR_COLUMNS]
        index = pd.DatetimeIndex(frame.index)
        frame.index = (
            index.tz_convert("UTC") if index.tz is not None else index.tz_localize("UTC")
        )
        return frame
