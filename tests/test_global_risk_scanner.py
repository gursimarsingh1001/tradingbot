from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import delete, select

from backend.db.models_investment import OfficialMarketContextSnapshot
from backend.db.postgres import GlobalRiskSnapshot, init_postgres, session_scope
from backend.engine.global_risk_scanner import GlobalRiskScanner


class _FakeHistoricalFetcher:
    def __init__(self, frame: pd.DataFrame):
        self.frame = frame

    def fetch_symbol_frame(self, _symbol_config):  # noqa: ANN001
        return self.frame.copy()


class _FakeNSEClient:
    def __init__(self, rows=None, *, exc: Exception | None = None):
        self.rows = list(rows or [])
        self.exc = exc

    def fetch_fii_dii_activity(self):
        if self.exc is not None:
            raise self.exc
        return list(self.rows)


class _FakeGlobalMarketClient:
    def __init__(self, *, sp500=None, crude=None, usdinr=None, exc: Exception | None = None):
        self.sp500 = sp500 or {"prev_close": 100.0, "latest_close": 100.0, "change_pct": 0.0}
        self.crude = crude or {"prev_close": 100.0, "latest_close": 100.0, "change_pct": 0.0}
        self.usdinr = usdinr or {"prev_close": 100.0, "latest_close": 100.0, "change_pct": 0.0}
        self.exc = exc

    def fetch_sp500(self):
        if self.exc is not None:
            raise self.exc
        return dict(self.sp500)

    def fetch_brent_crude(self):
        if self.exc is not None:
            raise self.exc
        return dict(self.crude)

    def fetch_usdinr(self):
        if self.exc is not None:
            raise self.exc
        return dict(self.usdinr)


def _make_nifty_frame(*, previous_day: date, latest_day: date, prev_close: float, latest_open: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [prev_close - 3.0, latest_open],
            "High": [prev_close + 5.0, latest_open + 4.0],
            "Low": [prev_close - 8.0, latest_open - 6.0],
            "Close": [prev_close, latest_open + 1.0],
            "Volume": [1_000_000.0, 1_100_000.0],
        },
        index=pd.DatetimeIndex([pd.Timestamp(previous_day), pd.Timestamp(latest_day)], tz="Asia/Kolkata"),
    )


def _seed_market_contexts(as_of_date: date, vix_values: list[float]) -> None:
    rows = []
    for offset, vix in enumerate(vix_values):
        rows.append(
            OfficialMarketContextSnapshot(
                as_of_date=as_of_date - timedelta(days=offset),
                nifty50_close=25000.0,
                nifty50_sma200=23000.0,
                india_vix=vix,
                sector_context={},
            )
        )
    with session_scope() as session:
        session.add_all(rows)


def _cleanup_dates(dates: list[date]) -> None:
    with session_scope() as session:
        session.execute(delete(GlobalRiskSnapshot).where(GlobalRiskSnapshot.as_of_date.in_(dates)))
        session.execute(delete(OfficialMarketContextSnapshot).where(OfficialMarketContextSnapshot.as_of_date.in_(dates)))


def test_global_risk_scanner_blocks_and_persists_snapshot():
    init_postgres()
    as_of_date = date.fromisoformat("2099-04-21")
    cleanup_dates = [as_of_date - timedelta(days=offset) for offset in range(8)]
    _cleanup_dates(cleanup_dates)
    _seed_market_contexts(as_of_date, [30.0, 20.0, 20.0, 20.0, 20.0, 20.0])

    scanner = GlobalRiskScanner(
        historical_fetcher=_FakeHistoricalFetcher(
            _make_nifty_frame(
                previous_day=as_of_date - timedelta(days=1),
                latest_day=as_of_date,
                prev_close=1000.0,
                latest_open=960.0,
            )
        ),
        nse_client=_FakeNSEClient(
            [
                {"category": "FII/FPI", "date": as_of_date, "buyValue": 1200.0, "sellValue": 1100.0, "netValue": 100.0},
                {"category": "DII", "date": as_of_date, "buyValue": 1500.0, "sellValue": 1400.0, "netValue": 100.0},
            ]
        ),
        global_market_client=_FakeGlobalMarketClient(),
    )

    result = scanner.scan(as_of_date, scan_type="AFTER_MARKET")

    assert result.risk_level == "RED"
    assert result.active_block_count >= 2
    assert {signal.name for signal in result.signals if signal.severity == "BLOCK"} >= {"vix_velocity", "nifty_gap"}

    with session_scope() as session:
        snapshot = session.scalar(
            select(GlobalRiskSnapshot).where(
                GlobalRiskSnapshot.as_of_date == as_of_date,
                GlobalRiskSnapshot.scan_type == "AFTER_MARKET",
            )
        )

    assert snapshot is not None
    assert snapshot.risk_level == "RED"
    assert snapshot.vix_severity == "BLOCK"
    assert snapshot.nifty_gap_severity == "BLOCK"

    _cleanup_dates(cleanup_dates)


def test_global_risk_scanner_fii_flow_uses_persisted_history():
    init_postgres()
    as_of_date = date.fromisoformat("2099-04-21")
    cleanup_dates = [as_of_date - timedelta(days=offset) for offset in range(8)]
    _cleanup_dates(cleanup_dates)
    with session_scope() as session:
        for offset, net in enumerate([-4500.0, -4300.0, -4200.0, -4100.0], start=1):
            snapshot_date = as_of_date - timedelta(days=offset)
            session.add(
                GlobalRiskSnapshot(
                    as_of_date=snapshot_date,
                    scan_type="AFTER_MARKET",
                    risk_level="YELLOW",
                    position_size_multiplier=0.5,
                    fii_net_today_crores=net,
                    signal_details={"fii_flow": {"flow_date": snapshot_date.isoformat()}},
                )
            )

    scanner = GlobalRiskScanner(
        historical_fetcher=_FakeHistoricalFetcher(pd.DataFrame()),
        nse_client=_FakeNSEClient(
            [
                {"category": "FII/FPI", "date": as_of_date, "buyValue": 1000.0, "sellValue": 4000.0, "netValue": -3500.0},
                {"category": "DII", "date": as_of_date, "buyValue": 5000.0, "sellValue": 3000.0, "netValue": 2000.0},
            ]
        ),
        global_market_client=_FakeGlobalMarketClient(),
    )

    signal = scanner.check_fii_flow(as_of_date)

    assert signal.severity == "BLOCK"
    assert signal.details["fii_consecutive_sell_days"] == 5
    assert signal.details["fii_cumulative_5day_crores"] <= -20000.0

    _cleanup_dates(cleanup_dates)


def test_global_risk_scanner_external_failures_are_fail_open():
    scanner = GlobalRiskScanner(
        historical_fetcher=_FakeHistoricalFetcher(pd.DataFrame()),
        nse_client=_FakeNSEClient([]),
        global_market_client=_FakeGlobalMarketClient(exc=RuntimeError("feed down")),
    )

    sp500 = scanner.check_us_market_overnight()
    crude = scanner.check_crude_oil()
    currency = scanner.check_currency_stress()

    assert sp500.severity == "NONE"
    assert crude.severity == "NONE"
    assert currency.severity == "NONE"
    assert sp500.details["skipped"] is True


def test_global_risk_scanner_pre_market_uses_carried_forward_gap_and_yellow_aggregation():
    init_postgres()
    as_of_date = date.fromisoformat("2099-04-21")
    latest_context_date = as_of_date - timedelta(days=1)
    cleanup_dates = [as_of_date - timedelta(days=offset) for offset in range(8)]
    _cleanup_dates(cleanup_dates)
    _seed_market_contexts(latest_context_date, [21.0, 20.0, 20.0, 20.0, 20.0, 20.0])

    scanner = GlobalRiskScanner(
        historical_fetcher=_FakeHistoricalFetcher(
            _make_nifty_frame(
                previous_day=latest_context_date - timedelta(days=1),
                latest_day=latest_context_date,
                prev_close=1000.0,
                latest_open=985.0,
            )
        ),
        nse_client=_FakeNSEClient(
            [
                {"category": "FII/FPI", "date": latest_context_date, "buyValue": 2200.0, "sellValue": 2100.0, "netValue": 100.0},
            ]
        ),
        global_market_client=_FakeGlobalMarketClient(
            sp500={"prev_close": 100.0, "latest_close": 97.5, "change_pct": -2.5},
            crude={"prev_close": 100.0, "latest_close": 104.0, "change_pct": 4.0},
            usdinr={"prev_close": 84.0, "latest_close": 84.3, "change_pct": 0.3571},
        ),
    )

    gap_signal = scanner.check_nifty_gap(as_of_date, scan_type="PRE_MARKET")
    result = scanner.scan(as_of_date, scan_type="PRE_MARKET")

    assert gap_signal.severity == "CAUTION"
    assert gap_signal.details["carried_forward"] is True
    assert result.risk_level == "YELLOW"
    assert result.position_size_multiplier == 0.5
    assert result.active_caution_count == 2

    _cleanup_dates(cleanup_dates)
