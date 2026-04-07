from __future__ import annotations

from datetime import date, datetime
from threading import Lock
from time import monotonic

from sqlalchemy import func, select

from backend.config import get_settings
from backend.data.angel_one_client import AngelOneClient, get_angel_one_client
from backend.db.postgres import PaperTrade, add_notification, get_config_value, session_scope, upsert_config_value
from backend.db.redis_client import get_cache


settings = get_settings()


class KillSwitch:
    DAILY_LOSS_LIMIT = -0.02
    PORTFOLIO_DD_LIMIT = -0.10
    API_TIMEOUT_SECONDS = 5
    MAX_CONSEC_ERRORS = 3
    VIX_SPIKE_THRESHOLD = 0.15
    MAX_POSITION_PCT = 0.20

    def __init__(self, angel_client: AngelOneClient | None = None) -> None:
        self.angel_client = angel_client or get_angel_one_client()
        self.cache = get_cache()
        self.consecutive_errors = 0
        self._api_health_lock = Lock()
        self._api_health_cached_value: bool | None = None
        self._api_health_cached_until: float = 0.0
        self._api_failure_notified_for_cache = False

    def daily_pnl_pct(self) -> float:
        trade_day = self._trading_day()
        with session_scope() as session:
            total_pnl_rupees = session.scalar(
                select(func.coalesce(func.sum(PaperTrade.pnl_rupees), 0.0)).where(PaperTrade.entry_date == trade_day)
            )
        base_portfolio = max(float(settings.paper_portfolio_value), 1.0)
        return float(total_pnl_rupees or 0.0) / base_portfolio

    @staticmethod
    def _trading_day() -> date:
        return datetime.now(tz=settings.tzinfo).date()

    def portfolio_drawdown(self) -> float:
        with session_scope() as session:
            peak = get_config_value(session, "peak_portfolio_value", {"value": 1_000_000})
            open_equity = session.scalar(select(func.coalesce(func.sum(PaperTrade.pnl_rupees), 0.0)))
        peak_value = float((peak or {}).get("value", 1_000_000))
        current_value = peak_value + float(open_equity or 0.0)
        if peak_value <= 0:
            return 0.0
        return (current_value - peak_value) / peak_value

    def angel_one_api_healthy(self) -> bool:
        now = monotonic()
        with self._api_health_lock:
            if self._api_health_cached_value is not None and now < self._api_health_cached_until:
                return self._api_health_cached_value

        healthy = self.angel_client.health_check()

        with self._api_health_lock:
            self._api_health_cached_value = healthy
            self._api_health_cached_until = now + max(settings.kill_switch_api_health_ttl_seconds, 1)
            if healthy:
                self._api_failure_notified_for_cache = False
        return healthy

    def _consume_api_failure_notification_slot(self) -> bool:
        with self._api_health_lock:
            if self._api_failure_notified_for_cache:
                return False
            self._api_failure_notified_for_cache = True
            return True

    def cancel_all_pending_orders(self) -> None:
        with session_scope() as session:
            add_notification(
                session,
                notification_type="KILL_SWITCH",
                title="Kill switch cancelled pending actions",
                body="New signals and pending paper actions were halted because the API health check failed.",
                color="red",
            )

    def vix_spike_today(self) -> float:
        try:
            indices = self.cache.get_json("live:indices", {}) or {}
        except Exception:
            return 0.0
        vix_row = indices.get("INDIA_VIX") or indices.get("INDIAVIX") or {}
        try:
            return max(float(vix_row.get("change_pct") or 0.0), 0.0)
        except (TypeError, ValueError):
            return 0.0

    def check_all(self) -> tuple[bool, str]:
        daily_pnl_pct = self.daily_pnl_pct()
        if daily_pnl_pct < self.DAILY_LOSS_LIMIT:
            return False, f"Daily loss limit hit: {daily_pnl_pct:.1%}"

        portfolio_drawdown = self.portfolio_drawdown()
        if portfolio_drawdown < self.PORTFOLIO_DD_LIMIT:
            return False, f"Max drawdown hit: {portfolio_drawdown:.1%}"

        if not self.angel_one_api_healthy():
            if self._consume_api_failure_notification_slot():
                self.cancel_all_pending_orders()
            return False, "Angel One API not responding"

        if self.consecutive_errors >= self.MAX_CONSEC_ERRORS:
            return False, f"{self.consecutive_errors} consecutive API errors"

        vix_spike = self.vix_spike_today()
        if vix_spike > self.VIX_SPIKE_THRESHOLD:
            return False, f"India VIX spiked {vix_spike:.1%} today"

        return True, "OK"

    def activate(self, reason: str) -> None:
        with session_scope() as session:
            upsert_config_value(session, "kill_switch", {"active": True, "reason": reason})
            add_notification(
                session,
                notification_type="KILL_SWITCH",
                title="Kill switch triggered",
                body=reason,
                color="red",
            )

    def restart(self, confirmed: bool) -> dict[str, str | bool | None]:
        if not confirmed:
            return {"active": True, "reason": "Restart requires confirmation."}
        with session_scope() as session:
            upsert_config_value(session, "kill_switch", {"active": False, "reason": None})
            add_notification(
                session,
                notification_type="KILL_SWITCH",
                title="Bot restarted",
                body="Trading bot resumed after manual confirmation.",
                color="blue",
            )
        return {"active": False, "reason": None}

    def current_state(self) -> dict[str, str | bool | None]:
        with session_scope() as session:
            value = get_config_value(session, "kill_switch", {"active": False, "reason": None})
        return value
