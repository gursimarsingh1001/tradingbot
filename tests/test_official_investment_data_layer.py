from __future__ import annotations

import json
from datetime import date

from sqlalchemy import delete, select

from backend.data.bse_client import BSEClient
from backend.data.historical_fetcher import HistoricalFetcher, SymbolConfig
from backend.data.nse_client import NSEClient
from backend.db.models_investment import (
    OfficialFinancialPeriod,
    OfficialInvestmentSnapshot,
    OfficialQuoteSnapshot,
    OfficialShareholdingSnapshot,
)
from backend.db.postgres import (
    BotConfig,
    StockFundamentalSnapshot,
    get_config_value,
    init_postgres,
    session_scope,
)
from backend.engine.official_investment_data_service import OfficialInvestmentDataService


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self):
        self.headers = {}
        self.calls: list[tuple[str, dict | None]] = []

    def mount(self, *_args, **_kwargs) -> None:
        return None

    def get(self, url, params=None, timeout=None):  # noqa: ANN001
        self.calls.append((url, params))
        if "quote-equity" in url:
            return _FakeResponse({"info": {"marketCap": "123456", "pe": "18.5"}})
        return _FakeResponse({"bootstrapped": True})


class _StubAngelClient:
    pass


class _QuoteOnlyNSEClient:
    def fetch_quote_equity(self, symbol: str):  # noqa: ANN001
        return {
            "info": {
                "marketCap": "1000000",
                "pe": "18.5",
                "dividendYield": "0.9",
                "industryPE": "22.1",
                "high52": "210",
                "low52": "120",
            }
        }

    @staticmethod
    def extract_quote_metrics(symbol: str, payload: dict):  # noqa: ANN001
        return NSEClient.extract_quote_metrics(symbol, payload)


class _WeeklyNSEClient:
    def fetch_financial_results(self, symbol: str, *, period: str = "Quarterly"):  # noqa: ANN001
        if period.lower() == "quarterly":
            return {
                "data": [
                    {
                        "quarterEnding": "2026-03-31",
                        "revenue": "1000",
                        "netProfit": "150",
                        "operatingProfit": "220",
                        "eps": "5.4",
                        "resultDate": "2026-04-25",
                    }
                ]
            }
        return {
            "data": [
                {
                    "fy": "2025-03-31",
                    "revenue": "3600",
                    "netProfit": "480",
                    "operatingProfit": "700",
                    "eps": "18.0",
                }
            ]
        }

    def fetch_shareholding(self, symbol: str):  # noqa: ANN001
        return {
            "data": [
                {
                    "category": "Promoter and Promoter Group",
                    "holdingPercent": "55.10",
                    "reportDate": "2026-03-31",
                },
                {"category": "FII / FPI", "holdingPercent": "13.40", "reportDate": "2026-03-31"},
                {"category": "DII", "holdingPercent": "9.80", "reportDate": "2026-03-31"},
            ]
        }

    @staticmethod
    def extract_financial_periods(symbol: str, payload: dict, *, period_type: str):  # noqa: ANN001
        return NSEClient.extract_financial_periods(symbol, payload, period_type=period_type)

    @staticmethod
    def extract_shareholding_snapshot(symbol: str, payload: dict):  # noqa: ANN001
        return NSEClient.extract_shareholding_snapshot(symbol, payload)


class _WeeklyBSEClient:
    def fetch_financial_results(self, scripcode: str):  # noqa: ANN001
        return {"Table": []}

    @staticmethod
    def extract_financial_periods(symbol: str, scripcode: str, payload: dict, *, period_type: str):  # noqa: ANN001
        return BSEClient.extract_financial_periods(symbol, scripcode, payload, period_type=period_type)


def test_historical_fetcher_merges_bse_symbol_mapping(tmp_path, monkeypatch):
    symbols_path = tmp_path / "symbols.json"
    mapping_path = tmp_path / "bse_mapping.json"
    symbols_path.write_text(
        json.dumps(
            [
                {
                    "symbol": "TESTCO",
                    "token": "123",
                    "companyName": "Test Co",
                    "exchange": "NSE",
                    "tradingSymbol": "TESTCO-EQ",
                }
            ]
        ),
        encoding="utf-8",
    )
    mapping_path.write_text(
        json.dumps(
            [
                {
                    "symbol": "TESTCO",
                    "bseScripcode": "500001",
                    "isin": "INE000A01010",
                    "canonicalExchange": "NSE",
                }
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr("backend.data.historical_fetcher.settings.symbols_config_path", symbols_path)
    monkeypatch.setattr("backend.data.historical_fetcher.settings.bse_symbol_mapping_path", mapping_path)

    fetcher = HistoricalFetcher()
    [symbol] = fetcher.load_symbols()

    assert symbol.symbol == "TESTCO"
    assert symbol.bse_scripcode == "500001"
    assert symbol.isin == "INE000A01010"
    assert symbol.canonical_exchange == "NSE"


def test_nse_client_bootstraps_session_before_api_calls():
    session = _FakeSession()
    client = NSEClient(session=session)

    payload = client.fetch_quote_equity("TESTCO")

    assert payload["info"]["marketCap"] == "123456"
    assert len(session.calls) == 2
    assert session.calls[0][0] == "https://www.nseindia.com"
    assert "quote-equity" in session.calls[1][0]


def test_nse_extractors_parse_quote_shareholding_and_financial_records():
    quote = NSEClient.extract_quote_metrics(
        "TESTCO",
        {
            "info": {
                "marketCap": "1500000000",
                "pe": "21.4",
                "dividendYield": "1.2",
                "industryPE": "24.1",
                "high52": "720",
                "low52": "410",
            }
        },
    )
    shareholding = NSEClient.extract_shareholding_snapshot(
        "TESTCO",
        {
            "data": [
                {"category": "Promoter and Promoter Group", "holdingPercent": "55.10", "reportDate": "2026-03-31"},
                {"category": "FII / FPI", "holdingPercent": "13.40", "reportDate": "2026-03-31"},
                {"category": "DII", "holdingPercent": "9.80", "reportDate": "2026-03-31"},
            ]
        },
    )
    periods = NSEClient.extract_financial_periods(
        "TESTCO",
        {
            "data": [
                {
                    "quarterEnding": "2026-03-31",
                    "revenue": "1000",
                    "netProfit": "150",
                    "operatingProfit": "220",
                    "eps": "5.4",
                    "resultDate": "2026-04-25",
                }
            ]
        },
        period_type="Quarterly",
    )

    assert quote["pe_ratio"] == 21.4
    assert shareholding["promoter_holding"] == 55.1
    assert shareholding["fii_holding"] == 13.4
    assert periods[0]["period_end"].isoformat() == "2026-03-31"
    assert periods[0]["earnings_date"].isoformat() == "2026-04-25"
    assert periods[0]["eps_basic"] == 5.4


def test_bse_extractors_parse_fallback_stock_info_and_financials():
    metrics = BSEClient.extract_stock_info_metrics(
        "TESTCO",
        "500001",
        {
            "data": {
                "marketCap": "2500000000",
                "peRatio": "19.2",
                "dividendYield": "0.8",
                "high52": "900",
                "low52": "500",
            }
        },
    )
    periods = BSEClient.extract_financial_periods(
        "TESTCO",
        "500001",
        {
            "Table": [
                {
                    "quarter": "2026-03-31",
                    "netProfit": "180",
                    "revenue": "1200",
                    "eps": "6.2",
                    "announcementDate": "2026-04-26",
                    "grossNpa": "1.3",
                    "capitalAdequacy": "14.5",
                }
            ]
        },
        period_type="Quarterly",
    )

    assert metrics["market_cap"] == 2500000000.0
    assert metrics["pe_ratio"] == 19.2
    assert periods[0]["earnings_date"].isoformat() == "2026-04-26"
    assert periods[0]["npa_pct"] == 1.3
    assert periods[0]["capital_adequacy_pct"] == 14.5


def test_official_service_calculates_eps_ttm_cagr_and_shadow_summary():
    quarters = [
        OfficialFinancialPeriod(symbol="TESTCO", period_type="QUARTERLY", period_end=date.fromisoformat("2026-03-31"), eps_basic=6.0),
        OfficialFinancialPeriod(symbol="TESTCO", period_type="QUARTERLY", period_end=date.fromisoformat("2025-12-31"), eps_basic=5.0),
        OfficialFinancialPeriod(symbol="TESTCO", period_type="QUARTERLY", period_end=date.fromisoformat("2025-09-30"), eps_basic=4.0),
        OfficialFinancialPeriod(symbol="TESTCO", period_type="QUARTERLY", period_end=date.fromisoformat("2025-06-30"), eps_basic=3.0),
        OfficialFinancialPeriod(symbol="TESTCO", period_type="QUARTERLY", period_end=date.fromisoformat("2025-03-31"), eps_basic=2.8),
        OfficialFinancialPeriod(symbol="TESTCO", period_type="QUARTERLY", period_end=date.fromisoformat("2024-12-31"), eps_basic=2.6),
        OfficialFinancialPeriod(symbol="TESTCO", period_type="QUARTERLY", period_end=date.fromisoformat("2024-09-30"), eps_basic=2.4),
        OfficialFinancialPeriod(symbol="TESTCO", period_type="QUARTERLY", period_end=date.fromisoformat("2024-06-30"), eps_basic=2.2),
        OfficialFinancialPeriod(symbol="TESTCO", period_type="QUARTERLY", period_end=date.fromisoformat("2024-03-31"), eps_basic=2.0),
        OfficialFinancialPeriod(symbol="TESTCO", period_type="QUARTERLY", period_end=date.fromisoformat("2023-12-31"), eps_basic=1.8),
        OfficialFinancialPeriod(symbol="TESTCO", period_type="QUARTERLY", period_end=date.fromisoformat("2023-09-30"), eps_basic=1.6),
        OfficialFinancialPeriod(symbol="TESTCO", period_type="QUARTERLY", period_end=date.fromisoformat("2023-06-30"), eps_basic=1.4),
        OfficialFinancialPeriod(symbol="TESTCO", period_type="QUARTERLY", period_end=date.fromisoformat("2023-03-31"), eps_basic=1.2),
        OfficialFinancialPeriod(symbol="TESTCO", period_type="QUARTERLY", period_end=date.fromisoformat("2022-12-31"), eps_basic=1.1),
        OfficialFinancialPeriod(symbol="TESTCO", period_type="QUARTERLY", period_end=date.fromisoformat("2022-09-30"), eps_basic=1.0),
        OfficialFinancialPeriod(symbol="TESTCO", period_type="QUARTERLY", period_end=date.fromisoformat("2022-06-30"), eps_basic=0.9),
    ]

    assert OfficialInvestmentDataService._calc_eps_ttm(quarters) == 18.0
    assert round(OfficialInvestmentDataService._calc_eps_growth_cagr(quarters), 2) == round((((18.0 / 4.2) ** (1 / 3)) - 1) * 100.0, 2)

    official = [
        OfficialInvestmentSnapshot(
            symbol="TESTCO",
            as_of_date=date.fromisoformat("2026-04-07"),
            pe_ratio=18.0,
            revenue_growth_pct=20.0,
            profit_growth_pct=25.0,
            roe=17.0,
            debt_to_equity=0.3,
            promoter_holding=54.0,
        )
    ]
    legacy = [
        StockFundamentalSnapshot(
            symbol="TESTCO",
            as_of_date=date.fromisoformat("2026-04-07"),
            pe_ratio=25.0,
            revenue_growth_pct=8.0,
            profit_growth_pct=10.0,
            roe=11.0,
            debt_to_equity=0.8,
            promoter_holding=50.0,
        )
    ]
    summary = OfficialInvestmentDataService._build_shadow_summary(
        official_rows=official,
        legacy_rows=legacy,
        missing_bse_mapping_symbols=["TESTCO"],
        recovered_by_bse_count=1,
    )

    assert summary["officialCoverage"] == 1
    assert summary["legacyCoverage"] == 1
    assert summary["recoveredByBse"] == 1
    assert summary["missingBseMappings"] == 1
    assert summary["materialDifferences"]["pe_ratio"] == 1


def test_official_quote_refresh_is_idempotent(monkeypatch):
    init_postgres()
    symbol = "ZZPHASE1Q"
    as_of_date = date.fromisoformat("2026-04-07")
    state_key = "test-official-shadow-quote-state"
    monkeypatch.setattr("backend.engine.official_investment_data_service.settings.official_shadow_quote_state_key", state_key)

    service = OfficialInvestmentDataService(
        nse_client=_QuoteOnlyNSEClient(),
        bse_client=BSEClient(),
        angel_client=_StubAngelClient(),
    )
    configs = [
        SymbolConfig(
            symbol=symbol,
            token="12345",
            company_name="Phase1 Quote Test",
            exchange="NSE",
            trading_symbol=f"{symbol}-EQ",
        )
    ]

    with session_scope() as session:
        session.execute(delete(OfficialQuoteSnapshot).where(OfficialQuoteSnapshot.symbol == symbol))
        session.execute(delete(BotConfig).where(BotConfig.key == state_key))

    service.refresh_quote_snapshots(symbol_configs=configs, as_of_date=as_of_date)
    service.refresh_quote_snapshots(symbol_configs=configs, as_of_date=as_of_date)

    with session_scope() as session:
        rows = session.scalars(
            select(OfficialQuoteSnapshot).where(
                OfficialQuoteSnapshot.symbol == symbol,
                OfficialQuoteSnapshot.as_of_date == as_of_date,
            )
        ).all()
        state = get_config_value(session, state_key, {})

    assert len(rows) == 1
    assert rows[0].pe_ratio == 18.5
    assert state["lastRequested"] == 1
    assert state["lastStored"] == 1

    with session_scope() as session:
        session.execute(delete(OfficialQuoteSnapshot).where(OfficialQuoteSnapshot.symbol == symbol))
        session.execute(delete(BotConfig).where(BotConfig.key == state_key))


def test_weekly_refresh_resumes_from_stored_offset(monkeypatch):
    init_postgres()
    symbols = ["ZZPHASE1W1", "ZZPHASE1W2"]
    state_key = "test-official-shadow-weekly-state"
    monkeypatch.setattr("backend.engine.official_investment_data_service.settings.official_shadow_weekly_state_key", state_key)

    service = OfficialInvestmentDataService(
        nse_client=_WeeklyNSEClient(),
        bse_client=_WeeklyBSEClient(),
        angel_client=_StubAngelClient(),
    )
    configs = [
        SymbolConfig(
            symbol=symbols[0],
            token="50001",
            company_name="Phase1 Weekly Test 1",
            exchange="NSE",
            trading_symbol=f"{symbols[0]}-EQ",
            bse_scripcode="500001",
        ),
        SymbolConfig(
            symbol=symbols[1],
            token="50002",
            company_name="Phase1 Weekly Test 2",
            exchange="NSE",
            trading_symbol=f"{symbols[1]}-EQ",
            bse_scripcode="500002",
        ),
    ]

    with session_scope() as session:
        session.execute(delete(OfficialFinancialPeriod).where(OfficialFinancialPeriod.symbol.in_(symbols)))
        session.execute(delete(OfficialShareholdingSnapshot).where(OfficialShareholdingSnapshot.symbol.in_(symbols)))
        session.execute(delete(BotConfig).where(BotConfig.key == state_key))

    first = service.refresh_weekly_fundamentals(symbol_configs=configs, batch_size=1)
    second = service.refresh_weekly_fundamentals(symbol_configs=configs, batch_size=1)

    with session_scope() as session:
        stored_periods = session.scalars(
            select(OfficialFinancialPeriod).where(OfficialFinancialPeriod.symbol.in_(symbols))
        ).all()
        stored_shareholding = session.scalars(
            select(OfficialShareholdingSnapshot).where(OfficialShareholdingSnapshot.symbol.in_(symbols))
        ).all()
        state = get_config_value(session, state_key, {})

    assert first["requested"] == 1
    assert first["next_offset"] == 1
    assert second["requested"] == 1
    assert second["next_offset"] == 0
    assert len({row.symbol for row in stored_periods}) == 2
    assert len({row.symbol for row in stored_shareholding}) == 2
    assert state["nextOffset"] == 0

    with session_scope() as session:
        session.execute(delete(OfficialFinancialPeriod).where(OfficialFinancialPeriod.symbol.in_(symbols)))
        session.execute(delete(OfficialShareholdingSnapshot).where(OfficialShareholdingSnapshot.symbol.in_(symbols)))
        session.execute(delete(BotConfig).where(BotConfig.key == state_key))
