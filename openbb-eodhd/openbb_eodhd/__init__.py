"""EODHD provider module for OpenBB."""

from openbb_core.provider.abstract.provider import Provider

from openbb_eodhd.models.calendar import (
    EODHDCalendarEarningsFetcher,
    EODHDCalendarIpoFetcher,
    EODHDCalendarSplitsFetcher,
    EODHDEconomicCalendarFetcher,
)
from openbb_eodhd.models.corporate_actions import (
    EODHDHistoricalDividendsFetcher,
    EODHDHistoricalSplitsFetcher,
)
from openbb_eodhd.models.crypto_historical import EODHDCryptoHistoricalFetcher
from openbb_eodhd.models.currency import (
    EODHDCurrencyPairsFetcher,
    EODHDCurrencySnapshotsFetcher,
)
from openbb_eodhd.models.currency_historical import EODHDCurrencyHistoricalFetcher
from openbb_eodhd.models.dividend_yield import EODHDTrailingDivYieldFetcher
from openbb_eodhd.models.equity_historical import EODHDEquityHistoricalFetcher
from openbb_eodhd.models.etf import (
    EODHDEtfCountriesFetcher,
    EODHDEtfHoldingsFetcher,
    EODHDEtfInfoFetcher,
    EODHDEtfSectorsFetcher,
)
from openbb_eodhd.models.estimates import (
    EODHDAnalystEstimatesFetcher,
    EODHDForwardEpsEstimatesFetcher,
    EODHDHistoricalEpsFetcher,
    EODHDPriceTargetConsensusFetcher,
)
from openbb_eodhd.models.fundamental import (
    EODHDBalanceSheetFetcher,
    EODHDCashFlowStatementFetcher,
    EODHDIncomeStatementFetcher,
)
from openbb_eodhd.models.government import EODHDGovernmentTradesFetcher
from openbb_eodhd.models.index import (
    EODHDAvailableIndicesFetcher,
    EODHDIndexHistoricalFetcher,
)
from openbb_eodhd.models.insider import EODHDInsiderTradingFetcher
from openbb_eodhd.models.market_cap import EODHDHistoricalMarketCapFetcher
from openbb_eodhd.models.news import EODHDWorldNewsFetcher
from openbb_eodhd.models.ownership import (
    EODHDEquityOwnershipFetcher,
    EODHDInstitutionalOwnershipFetcher,
)
from openbb_eodhd.models.screener import EODHDEquityScreenerFetcher
from openbb_eodhd.models.search import (
    EODHDCryptoSearchFetcher,
    EODHDEquitySearchFetcher,
    EODHDEtfSearchFetcher,
)
from openbb_eodhd.models.treasury import (
    EODHDTreasuryRatesFetcher,
    EODHDYieldCurveFetcher,
)

__all__ = [
    "EODHDEquityHistoricalFetcher",
    "EODHDCryptoHistoricalFetcher",
    "EODHDCurrencyHistoricalFetcher",
    "EODHDIncomeStatementFetcher",
    "EODHDBalanceSheetFetcher",
    "EODHDCashFlowStatementFetcher",
    "EODHDHistoricalDividendsFetcher",
    "EODHDHistoricalSplitsFetcher",
    "EODHDInstitutionalOwnershipFetcher",
    "EODHDEquityOwnershipFetcher",
    "EODHDInsiderTradingFetcher",
    "EODHDHistoricalEpsFetcher",
    "EODHDAnalystEstimatesFetcher",
    "EODHDForwardEpsEstimatesFetcher",
    "EODHDPriceTargetConsensusFetcher",
    "EODHDCalendarEarningsFetcher",
    "EODHDCalendarIpoFetcher",
    "EODHDCalendarSplitsFetcher",
    "EODHDEconomicCalendarFetcher",
    "EODHDEquitySearchFetcher",
    "EODHDEtfSearchFetcher",
    "EODHDCryptoSearchFetcher",
    "EODHDEquityScreenerFetcher",
    "EODHDCurrencyPairsFetcher",
    "EODHDCurrencySnapshotsFetcher",
    "EODHDAvailableIndicesFetcher",
    "EODHDIndexHistoricalFetcher",
    "EODHDHistoricalMarketCapFetcher",
    "EODHDWorldNewsFetcher",
    "EODHDTreasuryRatesFetcher",
    "EODHDYieldCurveFetcher",
    "EODHDGovernmentTradesFetcher",
    "EODHDEtfInfoFetcher",
    "EODHDEtfHoldingsFetcher",
    "EODHDEtfSectorsFetcher",
    "EODHDEtfCountriesFetcher",
    "EODHDTrailingDivYieldFetcher",
    "eodhd_provider",
]

eodhd_provider = Provider(
    name="eodhd",
    website="https://eodhd.com",
    description=(
        "EOD Historical Data (EODHD) APIs. Historical pricing for equities/ETFs, "
        "crypto, and forex (end-of-day + intraday); fundamentals (income, balance "
        "sheet, cash flow); corporate actions (dividends, splits); ownership "
        "(institutional and fund holders) and insider transactions; analyst "
        "estimates (historical EPS, forward estimates, price-target consensus); "
        "calendars (upcoming earnings, IPOs, splits, economic events); search, "
        "screener and index/forex reference data; ETF profiles and holdings; "
        "world news; US Treasury rates and yield curve; congressional trades; "
        "and historical market cap."
    ),
    # Becomes the credential field `eodhd_api_key` (env: EODHD_API_KEY).
    credentials=["api_key"],
    fetcher_dict={
        "EquityHistorical": EODHDEquityHistoricalFetcher,
        "EtfHistorical": EODHDEquityHistoricalFetcher,
        "CryptoHistorical": EODHDCryptoHistoricalFetcher,
        "CurrencyHistorical": EODHDCurrencyHistoricalFetcher,
        "IncomeStatement": EODHDIncomeStatementFetcher,
        "BalanceSheet": EODHDBalanceSheetFetcher,
        "CashFlowStatement": EODHDCashFlowStatementFetcher,
        "HistoricalDividends": EODHDHistoricalDividendsFetcher,
        "HistoricalSplits": EODHDHistoricalSplitsFetcher,
        "InstitutionalOwnership": EODHDInstitutionalOwnershipFetcher,
        "EquityOwnership": EODHDEquityOwnershipFetcher,
        "InsiderTrading": EODHDInsiderTradingFetcher,
        "HistoricalEps": EODHDHistoricalEpsFetcher,
        "AnalystEstimates": EODHDAnalystEstimatesFetcher,
        "ForwardEpsEstimates": EODHDForwardEpsEstimatesFetcher,
        "PriceTargetConsensus": EODHDPriceTargetConsensusFetcher,
        "CalendarEarnings": EODHDCalendarEarningsFetcher,
        "CalendarIpo": EODHDCalendarIpoFetcher,
        "CalendarSplits": EODHDCalendarSplitsFetcher,
        "EconomicCalendar": EODHDEconomicCalendarFetcher,
        "EquitySearch": EODHDEquitySearchFetcher,
        "EtfSearch": EODHDEtfSearchFetcher,
        "CryptoSearch": EODHDCryptoSearchFetcher,
        "EquityScreener": EODHDEquityScreenerFetcher,
        "CurrencyPairs": EODHDCurrencyPairsFetcher,
        "CurrencySnapshots": EODHDCurrencySnapshotsFetcher,
        "AvailableIndices": EODHDAvailableIndicesFetcher,
        "IndexHistorical": EODHDIndexHistoricalFetcher,
        "HistoricalMarketCap": EODHDHistoricalMarketCapFetcher,
        "WorldNews": EODHDWorldNewsFetcher,
        "TreasuryRates": EODHDTreasuryRatesFetcher,
        "YieldCurve": EODHDYieldCurveFetcher,
        "GovernmentTrades": EODHDGovernmentTradesFetcher,
        "EtfInfo": EODHDEtfInfoFetcher,
        "EtfHoldings": EODHDEtfHoldingsFetcher,
        "EtfSectors": EODHDEtfSectorsFetcher,
        "EtfCountries": EODHDEtfCountriesFetcher,
        "TrailingDividendYield": EODHDTrailingDivYieldFetcher,
    },
    repr_name="EOD Historical Data (EODHD)",
)
