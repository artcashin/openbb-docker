"""The fetcher: default window, cache metadata on the response, provider wiring."""

from datetime import date

from openbb_kdb.models.historical import KdbEquityHistoricalFetcher


def test_defaults_to_a_one_year_window():
    """The chart opens on 1 year; the default must match it."""
    q = KdbEquityHistoricalFetcher.transform_query({"symbol": "AAPL"})
    assert (q.end_date - q.start_date).days >= 364
    assert q.end_date <= date.today()


def test_explicit_dates_are_preserved():
    q = KdbEquityHistoricalFetcher.transform_query(
        {"symbol": "AAPL", "start_date": date(2022, 1, 1), "end_date": date(2024, 1, 1)}
    )
    assert (q.start_date, q.end_date) == (date(2022, 1, 1), date(2024, 1, 1))


def test_transform_data_attaches_cache_metadata():
    """The HUD reads this straight off extra['results_metadata']."""
    from openbb_core.provider.abstract.annotated_result import AnnotatedResult

    q = KdbEquityHistoricalFetcher.transform_query({"symbol": "AAPL"})
    rows = [{"date": date(2024, 1, 2), "open": 1.0, "high": 2.0,
             "low": 0.5, "close": 1.5, "volume": 100}]
    meta = {"cache": "hit", "rows_from_cache": 1, "rows_from_upstream": 0,
            "gaps_fetched": 0, "upstream_ms": 0.0, "kdb_ms": 1.2}
    out = KdbEquityHistoricalFetcher.transform_data(q, {"rows": rows, "meta": meta})
    assert isinstance(out, AnnotatedResult)
    assert out.metadata["cache"] == "hit"
    assert len(out.result) == 1


def test_provider_registers_all_five_models():
    from openbb_kdb import kdb_provider

    assert set(kdb_provider.fetcher_dict) == {
        "EquityHistorical", "EtfHistorical", "CryptoHistorical",
        "CurrencyHistorical", "IndexHistorical",
    }
