from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any

from sqlalchemy import select

from backend.config import get_settings
from backend.data.global_market_client import GlobalMarketClient, get_global_market_client
from backend.data.historical_fetcher import HistoricalFetcher, SymbolConfig
from backend.data.nse_client import NSEClient, get_nse_client
from backend.db.postgres import GlobalRiskSnapshot, OfficialMarketContextSnapshot, session_scope
from backend.engine.global_risk_types import GlobalRiskResult, GlobalRiskThresholds, SignalResult
from backend.engine.market_calendar import MarketCalendar, get_market_calendar
from backend.logging_utils import get_logger


settings = get_settings()
logger = get_logger(__name__)


class GlobalRiskScanner:
    NIFTY50_TARGET = SymbolConfig(
        symbol="NIFTY50",
        token="99926000",
        company_name="Nifty 50",
        exchange="NSE",
        trading_symbol="Nifty 50",
    )

    def __init__(
        self,
        *,
        historical_fetcher: HistoricalFetcher | None = None,
        nse_client: NSEClient | None = None,
        global_market_client: GlobalMarketClient | None = None,
        market_calendar: MarketCalendar | None = None,
    ) -> None:
        self.historical_fetcher = historical_fetcher or HistoricalFetcher()
        self.nse_client = nse_client or get_nse_client()
        self.global_market_client = global_market_client or get_global_market_client()
        self.market_calendar = market_calendar or get_market_calendar()

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        if parsed != parsed:
            return None
        return parsed

    @staticmethod
    def _summary_message(risk_level: str, caution_count: int, block_count: int) -> str:
        if risk_level == "RED" and block_count > 0:
            return f"RED risk: {block_count} block signal(s) active."
        if risk_level == "RED":
            return f"RED risk: {caution_count} caution signal(s) compounded."
        if risk_level == "YELLOW":
            return f"YELLOW risk: {caution_count} caution signal(s) active."
        return "GREEN risk: all clear."

    def _allow_carried_forward_internal(self, *, as_of_date: date, scan_type: str) -> bool:
        return scan_type == "PRE_MARKET" or not self.market_calendar.is_trading_day(as_of_date)

    def _recent_market_contexts(self, as_of_date: date, *, limit: int) -> list[OfficialMarketContextSnapshot]:
        with session_scope() as session:
            return session.scalars(
                select(OfficialMarketContextSnapshot)
                .where(OfficialMarketContextSnapshot.as_of_date <= as_of_date)
                .order_by(OfficialMarketContextSnapshot.as_of_date.desc())
                .limit(limit)
            ).all()

    def _nifty_frame(self, as_of_date: date):
        frame = self.historical_fetcher.fetch_symbol_frame(self.NIFTY50_TARGET)
        if getattr(frame, "empty", True):
            return frame
        if getattr(frame.index, "date", None) is not None:
            frame = frame[frame.index.date <= as_of_date]
        return frame

    def after_market_inputs_ready(self, as_of_date: date) -> bool:
        rows = self._recent_market_contexts(as_of_date, limit=max(settings.global_risk_vix_lookback_days + 1, 6))
        if not rows or rows[0].as_of_date != as_of_date:
            return False
        frame = self._nifty_frame(as_of_date)
        if getattr(frame, "empty", True) or len(frame) < 2:
            return False
        latest_day = frame.index[-1].date() if getattr(frame.index[-1], "date", None) is not None else None
        return latest_day == as_of_date

    def check_vix_velocity(self, as_of_date: date, *, scan_type: str) -> SignalResult:
        rows = self._recent_market_contexts(as_of_date, limit=max(settings.global_risk_vix_lookback_days + 1, 6))
        allow_carried_forward = self._allow_carried_forward_internal(as_of_date=as_of_date, scan_type=scan_type)
        if len(rows) < 6:
            return SignalResult("vix_velocity", "BLOCK", None, GlobalRiskThresholds.VIX_VELOCITY_BLOCK, "VIX history is incomplete, so crisis scan fails closed.", {"required_rows": 6, "available_rows": len(rows)})
        latest = rows[0]
        if latest.as_of_date != as_of_date and not allow_carried_forward:
            return SignalResult(
                "vix_velocity",
                "BLOCK",
                None,
                GlobalRiskThresholds.VIX_VELOCITY_BLOCK,
                "Current-day VIX context is missing, so crisis scan fails closed.",
                {
                    "latest_context_date": latest.as_of_date.isoformat() if latest.as_of_date else None,
                    "expected_as_of_date": as_of_date.isoformat(),
                },
            )
        current_vix = self._float(latest.india_vix)
        previous_values = [self._float(row.india_vix) for row in rows[1:6]]
        valid_previous = [value for value in previous_values if value is not None and value > 0]
        if current_vix is None or current_vix <= 0 or len(valid_previous) < 5:
            return SignalResult("vix_velocity", "BLOCK", None, GlobalRiskThresholds.VIX_VELOCITY_BLOCK, "VIX values are incomplete, so crisis scan fails closed.", {"current_vix": current_vix, "previous_count": len(valid_previous)})
        avg_vix = sum(valid_previous) / len(valid_previous)
        velocity_pct = ((current_vix - avg_vix) / avg_vix) * 100.0
        severity = "NONE"
        threshold = 0.0
        if velocity_pct >= GlobalRiskThresholds.VIX_VELOCITY_BLOCK:
            severity = "BLOCK"
            threshold = GlobalRiskThresholds.VIX_VELOCITY_BLOCK
        elif velocity_pct >= GlobalRiskThresholds.VIX_VELOCITY_CAUTION:
            severity = "CAUTION"
            threshold = GlobalRiskThresholds.VIX_VELOCITY_CAUTION
        return SignalResult(
            "vix_velocity",
            severity,
            round(velocity_pct, 4),
            threshold,
            f"India VIX is {velocity_pct:.2f}% above its prior 5-session average." if severity != "NONE" else "India VIX velocity is normal.",
            {
                "current_vix": round(current_vix, 4),
                "vix_5day_avg": round(avg_vix, 4),
                "latest_context_date": latest.as_of_date.isoformat() if latest.as_of_date else None,
                "carried_forward": latest.as_of_date != as_of_date,
            },
        )

    def check_nifty_gap(self, as_of_date: date, *, scan_type: str) -> SignalResult:
        frame = self._nifty_frame(as_of_date)
        if getattr(frame, "empty", True) or len(frame) < 2:
            return SignalResult("nifty_gap", "BLOCK", None, GlobalRiskThresholds.NIFTY_GAP_BLOCK, "Nifty OHLCV history is missing, so crisis scan fails closed.", {"rows": 0 if getattr(frame, "empty", True) else len(frame)})
        latest = frame.iloc[-1]
        previous = frame.iloc[-2]
        latest_day = frame.index[-1].date() if getattr(frame.index[-1], "date", None) is not None else None
        allow_carried_forward = self._allow_carried_forward_internal(as_of_date=as_of_date, scan_type=scan_type)
        if latest_day != as_of_date and not allow_carried_forward:
            return SignalResult("nifty_gap", "BLOCK", None, GlobalRiskThresholds.NIFTY_GAP_BLOCK, "Current-day Nifty gap data is missing, so crisis scan fails closed.", {"latest_bar_date": latest_day.isoformat() if latest_day else None, "expected_as_of_date": as_of_date.isoformat()})
        today_open = self._float(latest.get("Open"))
        prev_close = self._float(previous.get("Close"))
        if today_open is None or prev_close is None or prev_close <= 0:
            return SignalResult("nifty_gap", "BLOCK", None, GlobalRiskThresholds.NIFTY_GAP_BLOCK, "Nifty gap inputs are incomplete, so crisis scan fails closed.", {"today_open": today_open, "prev_close": prev_close})
        gap_pct = ((today_open - prev_close) / prev_close) * 100.0
        severity = "NONE"
        threshold = 0.0
        if gap_pct <= GlobalRiskThresholds.NIFTY_GAP_BLOCK:
            severity = "BLOCK"
            threshold = GlobalRiskThresholds.NIFTY_GAP_BLOCK
        elif gap_pct <= GlobalRiskThresholds.NIFTY_GAP_CAUTION:
            severity = "CAUTION"
            threshold = GlobalRiskThresholds.NIFTY_GAP_CAUTION
        return SignalResult(
            "nifty_gap",
            severity,
            round(gap_pct, 4),
            threshold,
            f"Nifty opened {gap_pct:.2f}% below the previous close." if severity != "NONE" else "Nifty gap is normal.",
            {
                "nifty_prev_close": round(prev_close, 4),
                "nifty_today_open": round(today_open, 4),
                "latest_bar_date": latest_day.isoformat() if latest_day else None,
                "carried_forward": latest_day != as_of_date,
            },
        )

    @staticmethod
    def _normalize_fii_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for row in rows:
            category = str(row.get("category") or "").upper()
            if not category:
                continue
            flow_date = row.get("date")
            normalized.append(
                {
                    "category": category,
                    "date": flow_date.isoformat() if hasattr(flow_date, "isoformat") else str(flow_date or ""),
                    "buy_value": GlobalRiskScanner._float(row.get("buyValue")),
                    "sell_value": GlobalRiskScanner._float(row.get("sellValue")),
                    "net_value": GlobalRiskScanner._float(row.get("netValue")),
                }
            )
        return normalized

    def _previous_fii_history(self, *, current_flow_date: str | None) -> list[tuple[str, float]]:
        with session_scope() as session:
            rows = session.scalars(
                select(GlobalRiskSnapshot)
                .where(GlobalRiskSnapshot.fii_net_today_crores.is_not(None))
                .order_by(GlobalRiskSnapshot.as_of_date.desc(), GlobalRiskSnapshot.created_at.desc())
                .limit(40)
            ).all()
        seen_dates: set[str] = set()
        history: list[tuple[str, float]] = []
        for row in rows:
            details = dict((row.signal_details or {}).get("fii_flow") or {})
            flow_date = str(details.get("flow_date") or row.as_of_date.isoformat())
            if not flow_date or flow_date == current_flow_date or flow_date in seen_dates:
                continue
            net_value = self._float(row.fii_net_today_crores)
            if net_value is None:
                continue
            seen_dates.add(flow_date)
            history.append((flow_date, net_value))
            if len(history) >= max(settings.global_risk_fii_lookback_days - 1, 1):
                break
        return history

    def check_fii_flow(self, as_of_date: date) -> SignalResult:
        try:
            rows = self.nse_client.fetch_fii_dii_activity()
        except Exception as exc:
            return SignalResult("fii_flow", "NONE", None, 0.0, "FII flow was skipped because the NSE endpoint was unavailable.", {"skipped": True, "error": f"{type(exc).__name__}: {exc}"})
        fii_row = next((row for row in rows if "FII" in str(row.get("category") or "").upper() or "FPI" in str(row.get("category") or "").upper()), None)
        if fii_row is None:
            return SignalResult("fii_flow", "NONE", None, 0.0, "FII flow was skipped because no FII/FPI row was returned.", {"skipped": True, "rows": self._normalize_fii_rows(rows)})
        flow_date_value = fii_row.get("date")
        flow_date = flow_date_value.isoformat() if hasattr(flow_date_value, "isoformat") else str(flow_date_value or as_of_date.isoformat())
        net_today = self._float(fii_row.get("netValue"))
        if net_today is None:
            return SignalResult("fii_flow", "NONE", None, 0.0, "FII flow was skipped because net flow was missing.", {"skipped": True, "row": self._normalize_fii_rows([fii_row])[0]})
        previous_history = self._previous_fii_history(current_flow_date=flow_date)
        series = [(flow_date, net_today)] + previous_history
        consecutive_sell_days = 0
        for _day, net_value in series:
            if net_value < 0:
                consecutive_sell_days += 1
            else:
                break
        cumulative_5day = sum(net_value for _day, net_value in series[:5])
        severity = "NONE"
        threshold = 0.0
        reasons: list[str] = []
        if cumulative_5day <= GlobalRiskThresholds.FII_CUMULATIVE_5DAY_BLOCK:
            severity = "BLOCK"
            threshold = GlobalRiskThresholds.FII_CUMULATIVE_5DAY_BLOCK
            reasons.append("fii_cumulative_5day_block")
        elif consecutive_sell_days >= GlobalRiskThresholds.FII_CONSECUTIVE_SELL_DAYS:
            severity = "CAUTION"
            threshold = float(GlobalRiskThresholds.FII_CONSECUTIVE_SELL_DAYS)
            reasons.append("fii_consecutive_sell_days")
        if net_today <= GlobalRiskThresholds.FII_HEAVY_SELL_SINGLE_DAY:
            if severity != "BLOCK":
                severity = "CAUTION"
                threshold = GlobalRiskThresholds.FII_HEAVY_SELL_SINGLE_DAY
            reasons.append("fii_heavy_sell_single_day")
        return SignalResult(
            "fii_flow",
            severity,
            round(net_today, 4),
            threshold,
            "FII flow is normal." if severity == "NONE" else "FII selling pressure is elevated." if severity == "CAUTION" else "FII selling pressure is severe.",
            {
                "fii_net_today_crores": round(net_today, 4),
                "fii_consecutive_sell_days": consecutive_sell_days,
                "fii_cumulative_5day_crores": round(cumulative_5day, 4),
                "flow_date": flow_date,
                "reasons": reasons,
                "history": [{"flow_date": day, "net_value": round(net, 4)} for day, net in series[: settings.global_risk_fii_lookback_days]],
                "raw_rows": self._normalize_fii_rows(rows),
            },
        )

    @staticmethod
    def _external_signal_from_quote(*, name: str, payload: dict[str, float], caution_threshold: float, block_threshold: float, is_negative: bool, label: str) -> SignalResult:
        change_pct = GlobalRiskScanner._float(payload.get("change_pct"))
        prev_close = GlobalRiskScanner._float(payload.get("prev_close"))
        latest_close = GlobalRiskScanner._float(payload.get("latest_close"))
        if change_pct is None or prev_close is None or latest_close is None:
            return SignalResult(name, "NONE", None, 0.0, f"{label} was skipped because the external feed returned incomplete data.", {"skipped": True, "payload": payload})
        severity = "NONE"
        threshold = 0.0
        if is_negative:
            if change_pct <= block_threshold:
                severity = "BLOCK"
                threshold = block_threshold
            elif change_pct <= caution_threshold:
                severity = "CAUTION"
                threshold = caution_threshold
        else:
            if change_pct >= block_threshold:
                severity = "BLOCK"
                threshold = block_threshold
            elif change_pct >= caution_threshold:
                severity = "CAUTION"
                threshold = caution_threshold
        return SignalResult(
            name,
            severity,
            round(change_pct, 4),
            threshold,
            f"{label} change is normal." if severity == "NONE" else f"{label} moved into caution territory." if severity == "CAUTION" else f"{label} moved into block territory.",
            {f"{name}_prev_close": round(prev_close, 4), f"{name}_latest_close": round(latest_close, 4)},
        )

    def check_us_market_overnight(self) -> SignalResult:
        try:
            payload = self.global_market_client.fetch_sp500()
        except Exception as exc:
            return SignalResult("sp500_overnight", "NONE", None, 0.0, "S&P 500 signal was skipped because Yahoo Finance was unavailable.", {"skipped": True, "error": f"{type(exc).__name__}: {exc}"})
        return self._external_signal_from_quote(name="sp500_overnight", payload=payload, caution_threshold=GlobalRiskThresholds.SP500_CAUTION, block_threshold=GlobalRiskThresholds.SP500_BLOCK, is_negative=True, label="S&P 500 overnight move")

    def check_crude_oil(self) -> SignalResult:
        try:
            payload = self.global_market_client.fetch_brent_crude()
        except Exception as exc:
            return SignalResult("crude_oil", "NONE", None, 0.0, "Crude oil signal was skipped because Yahoo Finance was unavailable.", {"skipped": True, "error": f"{type(exc).__name__}: {exc}"})
        return self._external_signal_from_quote(name="crude_oil", payload=payload, caution_threshold=GlobalRiskThresholds.CRUDE_SPIKE_CAUTION, block_threshold=GlobalRiskThresholds.CRUDE_SPIKE_BLOCK, is_negative=False, label="Brent crude move")

    def check_currency_stress(self) -> SignalResult:
        try:
            payload = self.global_market_client.fetch_usdinr()
        except Exception as exc:
            return SignalResult("currency_stress", "NONE", None, 0.0, "USD/INR signal was skipped because Yahoo Finance was unavailable.", {"skipped": True, "error": f"{type(exc).__name__}: {exc}"})
        return self._external_signal_from_quote(name="currency_stress", payload=payload, caution_threshold=GlobalRiskThresholds.USDINR_CAUTION, block_threshold=GlobalRiskThresholds.USDINR_BLOCK, is_negative=False, label="USD/INR move")

    @staticmethod
    def _compute_risk_level(signals: list[SignalResult]) -> str:
        block_count = sum(1 for signal in signals if signal.severity == "BLOCK")
        caution_count = sum(1 for signal in signals if signal.severity == "CAUTION")
        if block_count > 0:
            return "RED"
        if caution_count >= 3:
            return "RED"
        if caution_count >= 1:
            return "YELLOW"
        return "GREEN"

    @staticmethod
    def _multiplier_for(risk_level: str) -> float:
        return {"GREEN": 1.0, "YELLOW": 0.5, "RED": 0.0}[risk_level]

    def _upsert_snapshot(self, result: GlobalRiskResult) -> None:
        signal_map = {signal.name: signal for signal in result.signals}
        active_signals = [signal.name for signal in result.signals if signal.severity in {"CAUTION", "BLOCK"}]
        signal_details = {signal.name: asdict(signal) for signal in result.signals}
        vix_signal = signal_map.get("vix_velocity")
        gap_signal = signal_map.get("nifty_gap")
        fii_signal = signal_map.get("fii_flow")
        sp500_signal = signal_map.get("sp500_overnight")
        crude_signal = signal_map.get("crude_oil")
        currency_signal = signal_map.get("currency_stress")
        values = {
            "risk_level": result.risk_level,
            "position_size_multiplier": result.position_size_multiplier,
            "vix_current": self._float((vix_signal.details or {}).get("current_vix")) if vix_signal else None,
            "vix_5day_avg": self._float((vix_signal.details or {}).get("vix_5day_avg")) if vix_signal else None,
            "vix_velocity_pct": self._float(vix_signal.value) if vix_signal else None,
            "vix_severity": vix_signal.severity if vix_signal else "NONE",
            "nifty_prev_close": self._float((gap_signal.details or {}).get("nifty_prev_close")) if gap_signal else None,
            "nifty_today_open": self._float((gap_signal.details or {}).get("nifty_today_open")) if gap_signal else None,
            "nifty_gap_pct": self._float(gap_signal.value) if gap_signal else None,
            "nifty_gap_severity": gap_signal.severity if gap_signal else "NONE",
            "fii_net_today_crores": self._float((fii_signal.details or {}).get("fii_net_today_crores")) if fii_signal else None,
            "fii_consecutive_sell_days": int((fii_signal.details or {}).get("fii_consecutive_sell_days") or 0) or None if fii_signal else None,
            "fii_cumulative_5day_crores": self._float((fii_signal.details or {}).get("fii_cumulative_5day_crores")) if fii_signal else None,
            "fii_severity": fii_signal.severity if fii_signal else "NONE",
            "sp500_prev_close": self._float((sp500_signal.details or {}).get("sp500_overnight_prev_close")) if sp500_signal else None,
            "sp500_latest_close": self._float((sp500_signal.details or {}).get("sp500_overnight_latest_close")) if sp500_signal else None,
            "sp500_change_pct": self._float(sp500_signal.value) if sp500_signal else None,
            "sp500_severity": sp500_signal.severity if sp500_signal else "NONE",
            "crude_prev_close": self._float((crude_signal.details or {}).get("crude_oil_prev_close")) if crude_signal else None,
            "crude_latest_close": self._float((crude_signal.details or {}).get("crude_oil_latest_close")) if crude_signal else None,
            "crude_change_pct": self._float(crude_signal.value) if crude_signal else None,
            "crude_severity": crude_signal.severity if crude_signal else "NONE",
            "usdinr_prev_close": self._float((currency_signal.details or {}).get("currency_stress_prev_close")) if currency_signal else None,
            "usdinr_latest_close": self._float((currency_signal.details or {}).get("currency_stress_latest_close")) if currency_signal else None,
            "usdinr_change_pct": self._float(currency_signal.value) if currency_signal else None,
            "usdinr_severity": currency_signal.severity if currency_signal else "NONE",
            "active_signals": active_signals,
            "signal_details": signal_details,
        }
        with session_scope() as session:
            record = session.scalar(select(GlobalRiskSnapshot).where(GlobalRiskSnapshot.as_of_date == result.as_of_date, GlobalRiskSnapshot.scan_type == result.scan_type))
            if record is None:
                session.add(GlobalRiskSnapshot(as_of_date=result.as_of_date, scan_type=result.scan_type, **values))
            else:
                for key, value in values.items():
                    setattr(record, key, value)

    def scan(self, as_of_date: date, scan_type: str = "AFTER_MARKET") -> GlobalRiskResult:
        signals = [
            self.check_vix_velocity(as_of_date, scan_type=scan_type),
            self.check_nifty_gap(as_of_date, scan_type=scan_type),
            self.check_fii_flow(as_of_date),
            self.check_us_market_overnight(),
            self.check_crude_oil(),
            self.check_currency_stress(),
        ]
        risk_level = self._compute_risk_level(signals)
        caution_count = sum(1 for signal in signals if signal.severity == "CAUTION")
        block_count = sum(1 for signal in signals if signal.severity == "BLOCK")
        result = GlobalRiskResult(
            as_of_date=as_of_date,
            scan_type=scan_type,
            risk_level=risk_level,
            position_size_multiplier=self._multiplier_for(risk_level),
            signals=signals,
            active_caution_count=caution_count,
            active_block_count=block_count,
            summary_message=self._summary_message(risk_level, caution_count, block_count),
        )
        self._upsert_snapshot(result)
        logger.info(
            "Global risk scan for %s %s: level=%s multiplier=%.2f caution=%s block=%s active=%s",
            as_of_date.isoformat(),
            scan_type,
            result.risk_level,
            result.position_size_multiplier,
            result.active_caution_count,
            result.active_block_count,
            ",".join(signal.name for signal in signals if signal.severity in {"CAUTION", "BLOCK"}) or "none",
        )
        return result


__all__ = ["GlobalRiskScanner"]
