from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy import delete, select

from backend.data.bse_client import BSEClient
from backend.data.historical_fetcher import HistoricalFetcher, SymbolConfig
from backend.data.moneycontrol_client import MoneycontrolClient, MoneycontrolEarningsEvent
from backend.data.nse_client import NSEClient
from backend.data.screener_client import ScreenerCompanyData
from backend.db.models_investment import (
    OfficialCorporateAction,
    OfficialFinancialPeriod,
    OfficialInvestmentSnapshot,
    OfficialQuoteSnapshot,
    OfficialShareholdingSnapshot,
    ScreenerCache,
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
                    "totalAssets": "1200",
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


class _MissingShareholdingNSEClient(_WeeklyNSEClient):
    def fetch_shareholding(self, symbol: str):  # noqa: ANN001
        raise RuntimeError("NSE shareholding unavailable")


class _WeeklyBSEClient:
    def fetch_financial_results(self, scripcode: str):  # noqa: ANN001
        return {"Table": []}

    @staticmethod
    def extract_financial_periods(symbol: str, scripcode: str, payload: dict, *, period_type: str):  # noqa: ANN001
        return BSEClient.extract_financial_periods(symbol, scripcode, payload, period_type=period_type)


class _HybridBSEClient(_WeeklyBSEClient):
    def fetch_upcoming_board_meetings(self, scripcode: str, *, from_date: str | None = None, to_date: str | None = None):  # noqa: ANN001
        return {
            "Table": [
                {
                    "meetingDate": "2026-04-24",
                    "purpose": "Quarterly Results",
                    "description": "Board meeting to approve quarterly results",
                }
            ]
        }

    @staticmethod
    def extract_board_meeting_events(symbol: str, scripcode: str, payload: dict):  # noqa: ANN001
        return BSEClient.extract_board_meeting_events(symbol, scripcode, payload)


class _BrokenBoardMeetingsBSEClient(_WeeklyBSEClient):
    def fetch_upcoming_board_meetings(self, scripcode: str, *, from_date: str | None = None, to_date: str | None = None):  # noqa: ANN001
        raise RuntimeError("BSE API returned non-JSON response (text/html)")


class _MoneycontrolCalendarClient:
    def fetch_results_calendar_range(self, from_date: date, to_date: date):  # noqa: ANN001
        return [
            MoneycontrolEarningsEvent(
                symbol="TCS",
                company_name="Tata Consultancy Services",
                earnings_date=date.fromisoformat("2026-04-09"),
                source_url="https://www.moneycontrol.com/markets/earnings/results-calendar?activeDate=2026-04-09&id=All&name=All",
                raw_payload={
                    "scId": "TCS",
                    "stockShortName": "TCS",
                    "stockName": "Tata Consultancy Services",
                    "exchange": "N",
                },
            )
        ]


class _WeeklyScreenerClient:
    def fetch_company_data(self, symbol: str):  # noqa: ANN001
        return ScreenerCompanyData(
            symbol=symbol,
            company_name=f"{symbol} Limited",
            screener_slug=symbol.lower(),
            source_url=f"https://www.screener.in/company/{symbol}/",
            fetched_at="2026-04-07T18:00:00",
            top_ratios={
                "market_cap": 1000000.0,
                "pe_ratio": 18.5,
                "pb_ratio": 3.2,
                "dividend_yield": 1.2,
                "roe": 21.0,
                "roce": 23.0,
                "face_value": 10.0,
            },
            quarterly_ttm={
                "revenue_ttm": 420.0,
                "net_profit_ttm": 64.0,
                "eps_ttm": 16.0,
                "operating_profit_ttm": 90.0,
                "operating_margin_ttm": 21.4,
            },
            annual_latest={
                "revenue": 420.0,
                "net_profit": 64.0,
                "operating_profit": 90.0,
                "operating_margin": 21.4,
            },
            annual_previous={
                "revenue": 360.0,
                "net_profit": 48.0,
                "operating_profit": 75.0,
                "operating_margin": 20.8,
            },
            balance_sheet={
                "total_assets": 500.0,
                "total_debt": 70.0,
                "share_capital": 50.0,
                "reserves": 190.0,
                "current_assets": 150.0,
                "current_liabilities": 60.0,
            },
            cash_flow={"operating_cash_flow": 88.0},
            ratios={
                "debt_equity": 0.30,
                "current_ratio": 2.5,
                "interest_coverage": 12.2,
                "pb_ratio": 3.2,
            },
            shareholding_latest={
                "promoter_holding": 54.2,
                "fii_holding": 12.5,
                "dii_holding": 9.3,
            },
            shareholding_previous={
                "promoter_holding": 51.0,
                "fii_holding": 10.1,
                "dii_holding": 8.8,
            },
            computed={
                "promoter_holding_change_pct": 3.2,
                "asset_turnover": 0.84,
                "roa": 0.128,
                "gross_margin": 0.214,
                "shares_outstanding": 50000000.0,
            },
        )


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
                    "totalAssets": "400",
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
    assert periods[0]["total_assets"] == 400.0


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
                    "totalAssets": "600",
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
    assert periods[0]["total_assets"] == 600.0
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
        bse_client=_HybridBSEClient(),
        screener_client=_WeeklyScreenerClient(),
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
        session.execute(delete(OfficialCorporateAction).where(OfficialCorporateAction.symbol.in_(symbols)))
        session.execute(delete(ScreenerCache).where(ScreenerCache.symbol.in_(symbols)))
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
        stored_actions = session.scalars(
            select(OfficialCorporateAction).where(OfficialCorporateAction.symbol.in_(symbols))
        ).all()
        screener_cache_rows = session.scalars(
            select(ScreenerCache).where(ScreenerCache.symbol.in_(symbols))
        ).all()
        state = get_config_value(session, state_key, {})

    assert first["requested"] == 1
    assert first["next_offset"] == 1
    assert second["requested"] == 1
    assert second["next_offset"] == 0
    assert len({row.symbol for row in stored_periods}) == 2
    assert len({row.symbol for row in stored_shareholding}) == 2
    assert state["nextOffset"] == 0
    annual_periods = [row for row in stored_periods if row.period_type == "ANNUAL"]
    assert annual_periods
    assert any(row.total_assets == 1200.0 for row in annual_periods)
    assert any(row.source_status == "SCREENER_ONLY" for row in annual_periods)
    assert any(row.roa == 0.4 for row in annual_periods)
    assert any(row.asset_turnover == 3.0 for row in annual_periods)
    assert len(stored_actions) == 2
    assert len(screener_cache_rows) == 2

    with session_scope() as session:
        session.execute(delete(OfficialFinancialPeriod).where(OfficialFinancialPeriod.symbol.in_(symbols)))
        session.execute(delete(OfficialShareholdingSnapshot).where(OfficialShareholdingSnapshot.symbol.in_(symbols)))
        session.execute(delete(OfficialCorporateAction).where(OfficialCorporateAction.symbol.in_(symbols)))
        session.execute(delete(ScreenerCache).where(ScreenerCache.symbol.in_(symbols)))
        session.execute(delete(BotConfig).where(BotConfig.key == state_key))


def test_weekly_refresh_materializes_screener_shareholding_when_official_feed_missing(monkeypatch):
    init_postgres()
    monkeypatch.setattr("backend.engine.official_investment_data_service.settings.hybrid_data_enabled", True)
    monkeypatch.setattr("backend.engine.official_investment_data_service.settings.screener_enabled", True)
    symbol = "ZZPHASE6HOLD"

    with session_scope() as session:
        session.execute(delete(OfficialFinancialPeriod).where(OfficialFinancialPeriod.symbol == symbol))
        session.execute(delete(OfficialShareholdingSnapshot).where(OfficialShareholdingSnapshot.symbol == symbol))
        session.execute(delete(OfficialCorporateAction).where(OfficialCorporateAction.symbol == symbol))
        session.execute(delete(ScreenerCache).where(ScreenerCache.symbol == symbol))

    service = OfficialInvestmentDataService(
        nse_client=_MissingShareholdingNSEClient(),
        bse_client=_WeeklyBSEClient(),
        screener_client=_WeeklyScreenerClient(),
        angel_client=_StubAngelClient(),
    )

    result = service.refresh_weekly_fundamentals(
        symbol_configs=[
            SymbolConfig(
                symbol=symbol,
                token="51001",
                company_name="Phase 6 Holding Fallback",
                exchange="NSE",
                trading_symbol=f"{symbol}-EQ",
                bse_scripcode="500777",
            )
        ],
        batch_size=1,
    )

    with session_scope() as session:
        rows = session.scalars(
            select(OfficialShareholdingSnapshot)
            .where(OfficialShareholdingSnapshot.symbol == symbol)
            .order_by(OfficialShareholdingSnapshot.as_of_date.desc())
        ).all()

    assert result["stored_shareholding"] >= 2
    assert len(rows) == 2
    assert rows[0].promoter_holding == 54.2
    assert rows[1].promoter_holding == 51.0
    assert rows[0].source_status == "SCREENER_ONLY"
    assert rows[1].source_status == "SCREENER_ONLY"

    with session_scope() as session:
        session.execute(delete(OfficialFinancialPeriod).where(OfficialFinancialPeriod.symbol == symbol))
        session.execute(delete(OfficialShareholdingSnapshot).where(OfficialShareholdingSnapshot.symbol == symbol))
        session.execute(delete(OfficialCorporateAction).where(OfficialCorporateAction.symbol == symbol))
        session.execute(delete(ScreenerCache).where(ScreenerCache.symbol == symbol))


def test_bse_board_meeting_parser_extracts_earnings_event():
    events = BSEClient.extract_board_meeting_events(
        "TESTCO",
        "500001",
        {
            "Table": [
                {
                    "meetingDate": "2026-04-24",
                    "purpose": "Quarterly Results",
                    "description": "Board meeting to approve quarterly results",
                },
                {
                    "meetingDate": "2026-04-27",
                    "purpose": "Dividend",
                },
            ]
        },
    )

    assert len(events) == 1
    assert events[0]["meeting_date"].isoformat() == "2026-04-24"
    assert events[0]["action_type"] == "BOARD_MEETING_RESULTS"


def test_moneycontrol_results_calendar_parser_handles_current_page_shape():
    html = """
    <html>
      <body>
        <script id="__NEXT_DATA__" type="application/json">
          {
            "props": {
              "pageProps": {
                "resultCalendarData": {
                  "tableData": {
                    "list": [
                      {
                        "date": "9 Apr",
                        "stockName": "Tata Consultancy Services",
                        "stockShortName": "TCS",
                        "scId": "TCS",
                        "exchange": "N",
                        "resultType": "Q4 FY25-26"
                      }
                    ]
                  }
                }
              }
            }
          }
        </script>
      </body>
    </html>
    """

    events = MoneycontrolClient.parse_results_calendar_html(
        html,
        active_date=date.fromisoformat("2026-04-09"),
        source_url="https://www.moneycontrol.com/markets/earnings/results-calendar?activeDate=2026-04-09&id=All&name=All",
    )

    assert len(events) == 1
    assert events[0].symbol == "TCS"
    assert events[0].company_name == "Tata Consultancy Services"
    assert events[0].earnings_date.isoformat() == "2026-04-09"


def test_refresh_upcoming_earnings_calendar_uses_moneycontrol_bulk_fallback(monkeypatch):
    init_postgres()
    monkeypatch.setattr("backend.engine.official_investment_data_service.settings.hybrid_data_enabled", True)
    monkeypatch.setattr("backend.engine.official_investment_data_service.settings.moneycontrol_enabled", True)
    monkeypatch.setattr("backend.engine.official_investment_data_service.settings.bse_board_meetings_enabled", True)

    symbol = "TCS"
    with session_scope() as session:
        session.execute(delete(OfficialCorporateAction).where(OfficialCorporateAction.symbol == symbol))

    service = OfficialInvestmentDataService(
        nse_client=_WeeklyNSEClient(),
        bse_client=_BrokenBoardMeetingsBSEClient(),
        screener_client=_WeeklyScreenerClient(),
        moneycontrol_client=_MoneycontrolCalendarClient(),
        angel_client=_StubAngelClient(),
    )

    result = service.refresh_upcoming_earnings_calendar(
        symbol_configs=[
            SymbolConfig(
                symbol="TCS",
                token="11536",
                company_name="Tata Consultancy Services Ltd.",
                trading_symbol="TCS-EQ",
                bse_scripcode="532540",
            )
        ],
        as_of_date=date.fromisoformat("2026-04-08"),
    )

    with session_scope() as session:
        action = session.scalar(
            select(OfficialCorporateAction).where(
                OfficialCorporateAction.symbol == symbol,
                OfficialCorporateAction.action_type == "MONEYCONTROL_EARNINGS",
            )
        )

    assert result["stored"] == 1
    assert action is not None
    assert action.ex_date.isoformat() == "2026-04-09"
    assert action.source_status == "MONEYCONTROL_EARNINGS"

    with session_scope() as session:
        session.execute(delete(OfficialCorporateAction).where(OfficialCorporateAction.symbol == symbol))


def test_snapshot_rebuild_uses_cached_screener_data_and_board_meeting(monkeypatch):
    init_postgres()
    symbol = "ZZPHASE6SNAP"
    as_of_date = date.fromisoformat("2026-04-07")
    monkeypatch.setattr("backend.engine.official_investment_data_service.settings.hybrid_data_enabled", True)

    with session_scope() as session:
        session.execute(delete(OfficialInvestmentSnapshot).where(OfficialInvestmentSnapshot.symbol == symbol))
        session.execute(delete(OfficialQuoteSnapshot).where(OfficialQuoteSnapshot.symbol == symbol))
        session.execute(delete(OfficialFinancialPeriod).where(OfficialFinancialPeriod.symbol == symbol))
        session.execute(delete(OfficialShareholdingSnapshot).where(OfficialShareholdingSnapshot.symbol == symbol))
        session.execute(delete(OfficialCorporateAction).where(OfficialCorporateAction.symbol == symbol))
        session.execute(delete(ScreenerCache).where(ScreenerCache.symbol == symbol))

        session.add(
            OfficialQuoteSnapshot(
                symbol=symbol,
                company_name="Phase 6 Snapshot",
                sector="IT",
                as_of_date=as_of_date,
                source_status="NSE_ONLY",
                market_cap=500000000.0,
                pe_ratio=18.0,
                dividend_yield=None,
                industry_pe=22.0,
                week_52_high=210.0,
                week_52_low=120.0,
                metadata_json={},
            )
        )
        session.add(
            OfficialFinancialPeriod(
                symbol=symbol,
                period_type="QUARTERLY",
                period_end=date.fromisoformat("2026-03-31"),
                revenue=100.0,
                net_profit=20.0,
                operating_profit=30.0,
                eps_basic=5.0,
                earnings_date=None,
            )
        )
        session.add(
            OfficialShareholdingSnapshot(
                symbol=symbol,
                as_of_date=date.fromisoformat("2026-03-31"),
                promoter_holding=52.0,
                promoter_pledge=1.0,
                fii_holding=10.0,
                dii_holding=8.0,
                source_status="NSE_ONLY",
            )
        )
        session.add(
            OfficialCorporateAction(
                symbol=symbol,
                ex_date=date.fromisoformat("2026-04-24"),
                action_type="BOARD_MEETING_RESULTS",
                description="Quarterly results board meeting",
                source_status="BOARD_MEETING_RESULTS",
                raw_payload={},
            )
        )
        session.add(
            ScreenerCache(
                symbol=symbol,
                company_name="Phase 6 Snapshot",
                screener_slug=symbol.lower(),
                source_url=f"https://www.screener.in/company/{symbol}/",
                fetched_at=datetime.fromisoformat("2026-04-07T18:00:00"),
                data_json=_WeeklyScreenerClient().fetch_company_data(symbol).to_cache_payload(),
                raw_payload={},
            )
        )

    service = OfficialInvestmentDataService(
        nse_client=_QuoteOnlyNSEClient(),
        bse_client=_HybridBSEClient(),
        screener_client=_WeeklyScreenerClient(),
        angel_client=_StubAngelClient(),
    )
    service.rebuild_official_investment_snapshots(as_of_date=as_of_date)

    with session_scope() as session:
        snapshot = session.scalar(
            select(OfficialInvestmentSnapshot).where(
                OfficialInvestmentSnapshot.symbol == symbol,
                OfficialInvestmentSnapshot.as_of_date == as_of_date,
            )
        )

    assert snapshot is not None
    assert snapshot.pb_ratio == 3.2
    assert snapshot.dividend_yield == 1.2
    assert snapshot.earnings_date.isoformat() == "2026-04-24"
    assert snapshot.data_sources["fields"]["pb_ratio"]["selected_source"] == "SCREENER"
    assert snapshot.source_coverage["has_board_meeting_earnings"] is True

    with session_scope() as session:
        session.execute(delete(OfficialInvestmentSnapshot).where(OfficialInvestmentSnapshot.symbol == symbol))
        session.execute(delete(OfficialQuoteSnapshot).where(OfficialQuoteSnapshot.symbol == symbol))
        session.execute(delete(OfficialFinancialPeriod).where(OfficialFinancialPeriod.symbol == symbol))
        session.execute(delete(OfficialShareholdingSnapshot).where(OfficialShareholdingSnapshot.symbol == symbol))
        session.execute(delete(OfficialCorporateAction).where(OfficialCorporateAction.symbol == symbol))
        session.execute(delete(ScreenerCache).where(ScreenerCache.symbol == symbol))


def test_snapshot_rebuild_prefers_fresh_screener_for_accounting_and_shareholding(monkeypatch):
    init_postgres()
    symbol = "ZZPHASE6PREF"
    as_of_date = date.fromisoformat("2026-04-07")
    monkeypatch.setattr("backend.engine.official_investment_data_service.settings.hybrid_data_enabled", True)

    with session_scope() as session:
        session.execute(delete(OfficialInvestmentSnapshot).where(OfficialInvestmentSnapshot.symbol == symbol))
        session.execute(delete(OfficialQuoteSnapshot).where(OfficialQuoteSnapshot.symbol == symbol))
        session.execute(delete(OfficialFinancialPeriod).where(OfficialFinancialPeriod.symbol == symbol))
        session.execute(delete(OfficialShareholdingSnapshot).where(OfficialShareholdingSnapshot.symbol == symbol))
        session.execute(delete(ScreenerCache).where(ScreenerCache.symbol == symbol))

        session.add(
            OfficialQuoteSnapshot(
                symbol=symbol,
                company_name="Phase 6 Pref Fresh Screener",
                sector="IT",
                as_of_date=as_of_date,
                source_status="NSE_ONLY",
                market_cap=500000000.0,
                pe_ratio=18.0,
                dividend_yield=0.8,
                industry_pe=22.0,
                week_52_high=210.0,
                week_52_low=120.0,
                metadata_json={},
            )
        )
        session.add(
            OfficialFinancialPeriod(
                symbol=symbol,
                period_type="ANNUAL",
                period_end=date.fromisoformat("2025-03-31"),
                revenue=300.0,
                net_profit=30.0,
                operating_profit=45.0,
                total_debt=90.0,
                current_assets=110.0,
                current_liabilities=70.0,
                shareholder_equity=240.0,
                capital_employed=330.0,
                gross_margin=0.15,
                asset_turnover=0.60,
                roa=0.08,
                source_status="NSE_ONLY",
            )
        )
        session.add(
            OfficialShareholdingSnapshot(
                symbol=symbol,
                as_of_date=date.fromisoformat("2025-12-31"),
                promoter_holding=50.0,
                promoter_pledge=1.0,
                fii_holding=8.0,
                dii_holding=7.0,
                source_status="NSE_ONLY",
            )
        )
        session.add(
            ScreenerCache(
                symbol=symbol,
                company_name="Phase 6 Pref Fresh Screener",
                screener_slug=symbol.lower(),
                source_url=f"https://www.screener.in/company/{symbol}/",
                fetched_at=datetime.fromisoformat("2026-04-07T18:00:00"),
                data_json=_WeeklyScreenerClient().fetch_company_data(symbol).to_cache_payload(),
                raw_payload={},
            )
        )

    service = OfficialInvestmentDataService(
        nse_client=_QuoteOnlyNSEClient(),
        bse_client=_HybridBSEClient(),
        screener_client=_WeeklyScreenerClient(),
        angel_client=_StubAngelClient(),
    )
    service.rebuild_official_investment_snapshots(as_of_date=as_of_date)

    with session_scope() as session:
        snapshot = session.scalar(
            select(OfficialInvestmentSnapshot).where(
                OfficialInvestmentSnapshot.symbol == symbol,
                OfficialInvestmentSnapshot.as_of_date == as_of_date,
            )
        )

    assert snapshot is not None
    assert snapshot.roe == 21.0
    assert snapshot.promoter_holding == 54.2
    assert snapshot.data_sources["fields"]["roe"]["selected_source"] == "SCREENER"
    assert snapshot.data_sources["fields"]["promoter_holding"]["selected_source"] == "SCREENER"

    with session_scope() as session:
        session.execute(delete(OfficialInvestmentSnapshot).where(OfficialInvestmentSnapshot.symbol == symbol))
        session.execute(delete(OfficialQuoteSnapshot).where(OfficialQuoteSnapshot.symbol == symbol))
        session.execute(delete(OfficialFinancialPeriod).where(OfficialFinancialPeriod.symbol == symbol))
        session.execute(delete(OfficialShareholdingSnapshot).where(OfficialShareholdingSnapshot.symbol == symbol))
        session.execute(delete(ScreenerCache).where(ScreenerCache.symbol == symbol))


def test_snapshot_rebuild_reuses_stale_screener_cache_without_data_loss(monkeypatch):
    init_postgres()
    symbol = "ZZPHASE6STALE"
    as_of_date = date.fromisoformat("2026-04-07")
    monkeypatch.setattr("backend.engine.official_investment_data_service.settings.hybrid_data_enabled", True)
    stale_fetched_at = datetime.fromisoformat("2026-03-20T18:00:00")

    with session_scope() as session:
        session.execute(delete(OfficialInvestmentSnapshot).where(OfficialInvestmentSnapshot.symbol == symbol))
        session.execute(delete(OfficialQuoteSnapshot).where(OfficialQuoteSnapshot.symbol == symbol))
        session.execute(delete(ScreenerCache).where(ScreenerCache.symbol == symbol))
        session.add(
            OfficialQuoteSnapshot(
                symbol=symbol,
                company_name="Phase 6 Stale",
                sector="IT",
                as_of_date=as_of_date,
                source_status="NSE_ONLY",
                market_cap=200000000.0,
                pe_ratio=None,
                dividend_yield=None,
                industry_pe=18.0,
                week_52_high=110.0,
                week_52_low=55.0,
                metadata_json={},
            )
        )
        session.add(
            ScreenerCache(
                symbol=symbol,
                company_name="Phase 6 Stale",
                screener_slug=symbol.lower(),
                source_url=f"https://www.screener.in/company/{symbol}/",
                fetched_at=stale_fetched_at,
                data_json=_WeeklyScreenerClient().fetch_company_data(symbol).to_cache_payload(),
                raw_payload={},
            )
        )

    service = OfficialInvestmentDataService(
        nse_client=_QuoteOnlyNSEClient(),
        bse_client=_HybridBSEClient(),
        screener_client=_WeeklyScreenerClient(),
        angel_client=_StubAngelClient(),
    )
    service.rebuild_official_investment_snapshots(as_of_date=as_of_date)

    with session_scope() as session:
        snapshot = session.scalar(
            select(OfficialInvestmentSnapshot).where(
                OfficialInvestmentSnapshot.symbol == symbol,
                OfficialInvestmentSnapshot.as_of_date == as_of_date,
            )
        )

    assert snapshot is not None
    assert snapshot.pe_ratio == 18.5
    assert snapshot.source_coverage["screener_cache_stale"] is True
    assert snapshot.raw_metrics["screener_cache_age_days"] >= 7

    with session_scope() as session:
        session.execute(delete(OfficialInvestmentSnapshot).where(OfficialInvestmentSnapshot.symbol == symbol))
        session.execute(delete(OfficialQuoteSnapshot).where(OfficialQuoteSnapshot.symbol == symbol))
        session.execute(delete(ScreenerCache).where(ScreenerCache.symbol == symbol))
