from openbb_eodhd import eodhd_provider


def test_phase1_models_registered():
    for key in [
        "InstitutionalOwnership", "EquityOwnership", "InsiderTrading",
        "HistoricalEps", "AnalystEstimates", "ForwardEpsEstimates",
        "PriceTargetConsensus",
    ]:
        assert key in eodhd_provider.fetcher_dict, f"missing {key}"
