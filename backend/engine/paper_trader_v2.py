from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from sqlalchemy import select

from backend.config import get_settings
from backend.db.postgres import MistakeLog, PaperTrade, add_notification, session_scope
from backend.engine.kill_switch import KillSwitch
from backend.engine.market_calendar import get_market_calendar
from backend.engine.position_sizer import calculate_size
from backend.engine.regime_detector import regime_is_high_volatility, regime_trend_direction


settings = get_settings()


class PaperTrader:
    INTRADAY_MARGIN_CAP = 5.0
    TARGET_EXIT_FRACTIONS = {"T1": 0.50, "T2": 0.30, "T3": 1.0}

    def __init__(self, kill_switch: KillSwitch | None = None) -> None:
        self.kill_switch = kill_switch or KillSwitch()
        self.market_calendar = get_market_calendar()

    @staticmethod
    def _market_open_time() -> time:
        return time.fromisoformat(settings.market_open_time)

    @staticmethod
    def _intraday_entry_cutoff() -> time:
        return time.fromisoformat(settings.intraday_entry_cutoff_time)

    @staticmethod
    def _intraday_square_off_time() -> time:
        return time.fromisoformat(settings.intraday_cutoff_time)

    @staticmethod
    def _apply_entry_slippage(price: float, direction: str) -> float:
        return price * (1.001 if direction == "BUY" else 0.999)

    @staticmethod
    def _apply_exit_slippage(price: float, direction: str) -> float:
        return price * (0.999 if direction == "BUY" else 1.001)

    @staticmethod
    def _atr_proxy(entry_price: float) -> float:
        return max(entry_price * 0.01, 1.0)

    @staticmethod
    def _plan_levels(entry_price: float, direction: str = "BUY") -> tuple[float, float, float, float]:
        atr_proxy = PaperTrader._atr_proxy(entry_price)
        if direction.upper() == "SELL":
            stop_loss = entry_price + (2 * atr_proxy)
            target_1 = max(entry_price - (2 * atr_proxy), 0.01)
            target_2 = max(entry_price - (4 * atr_proxy), 0.01)
            target_3 = max(entry_price - (6 * atr_proxy), 0.01)
            return stop_loss, target_1, target_2, target_3
        stop_loss = max(entry_price - (2 * atr_proxy), 0.01)
        target_1 = entry_price + (2 * atr_proxy)
        target_2 = entry_price + (4 * atr_proxy)
        target_3 = entry_price + (6 * atr_proxy)
        return stop_loss, target_1, target_2, target_3

    @staticmethod
    def _float(value: Any, default: float = 0.0) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        if parsed != parsed:
            return default
        return parsed

    @staticmethod
    def _entry_costs(entry_price: float, shares: int, direction: str) -> float:
        turnover_entry = entry_price * shares
        brokerage = 20.0
        exchange_charge = turnover_entry * 0.0005
        stt = turnover_entry * 0.001 if direction == "SELL" else 0.0
        return brokerage + exchange_charge + stt

    @staticmethod
    def _exit_costs(exit_price: float, shares: int, direction: str) -> float:
        turnover_exit = exit_price * shares
        brokerage = 20.0
        exchange_charge = turnover_exit * 0.0005
        stt = turnover_exit * 0.001 if direction == "BUY" else 0.0
        return brokerage + exchange_charge + stt

    def _add_trading_days(self, start_day: date, sessions: int) -> date:
        cursor = start_day
        for _ in range(max(sessions, 0)):
            cursor = self.market_calendar.next_trading_day(cursor)
        return cursor

    @staticmethod
    def _trade_metadata(trade: PaperTrade) -> dict[str, Any]:
        metadata = dict(trade.metadata_json or {})
        metadata.setdefault("direction", "BUY")
        metadata.setdefault("remaining_shares", int(trade.shares or 0))
        metadata.setdefault("initial_shares", int(trade.shares or 0))
        metadata.setdefault("remaining_entry_costs", 0.0)
        metadata.setdefault("realized_pnl_rupees", 0.0)
        metadata.setdefault("partials", [])
        metadata.setdefault("partial_exit_plan", dict(PaperTrader.TARGET_EXIT_FRACTIONS))
        metadata.setdefault("plan_only", False)
        metadata.setdefault("plan_status", "PLANNED" if metadata.get("plan_only") else "OPEN")
        metadata.setdefault(
            "allow_same_day_activation",
            bool(metadata.get("opened_from") == "after_market_watchlist"),
        )
        return metadata

    @staticmethod
    def _bucket_key(signal_type: str | None) -> str:
        return "INVESTMENT" if str(signal_type or "").upper() == "INVESTMENT" else "INTRADAY"

    @staticmethod
    def _bucket_label(bucket_key: str) -> str:
        return "Investment" if bucket_key == "INVESTMENT" else "Intraday"

    def _bucket_allocation_pct(self, signal_type: str | None) -> float:
        bucket_key = self._bucket_key(signal_type)
        if bucket_key == "INVESTMENT":
            return self._float(settings.paper_investment_allocation_pct, 0.50)
        return self._float(settings.paper_intraday_allocation_pct, 0.50)

    def _bucket_base_capital(self, signal_type: str | None) -> float:
        allocation_pct = self._bucket_allocation_pct(signal_type)
        return max(settings.paper_portfolio_value * allocation_pct, 0.0)

    def _bucket_risk_budget(self, signal_type: str | None, session=None) -> float:
        target_capital = self._bucket_target_capital(signal_type, session=session)
        return max(target_capital * max(float(settings.paper_max_open_risk_pct), 0.0), 0.0)

    def _bucket_target_capital(self, signal_type: str | None, session=None) -> float:
        base_capital = self._bucket_base_capital(signal_type)
        if session is None:
            return base_capital
        bucket_key = self._bucket_key(signal_type)
        pnl_total = 0.0
        trades = session.scalars(select(PaperTrade)).all()
        for trade in trades:
            metadata = self._trade_metadata(trade)
            if metadata.get("plan_only"):
                continue
            if self._bucket_key(trade.signal_type) != bucket_key:
                continue
            pnl_total += self._float(trade.pnl_rupees)
        return max(base_capital + pnl_total, 0.0)

    def _bucket_usage(self, session, signal_type: str | None, *, include_planned: bool, exclude_trade_id: str | None = None) -> float:
        bucket_key = self._bucket_key(signal_type)
        trades = session.scalars(select(PaperTrade).where(PaperTrade.exit_date.is_(None))).all()
        total = 0.0
        for trade in trades:
            if exclude_trade_id and trade.trade_id == exclude_trade_id:
                continue
            metadata = self._trade_metadata(trade)
            if self._bucket_key(trade.signal_type) != bucket_key:
                continue
            if metadata.get("plan_only") and not include_planned:
                continue
            total += self._float(metadata.get("capital_blocked"))
        return total

    def _trade_risk_amount(self, trade: PaperTrade) -> float:
        metadata = self._trade_metadata(trade)
        remaining_shares = int(metadata.get("remaining_shares", trade.shares or 0))
        if remaining_shares <= 0 or trade.exit_date is not None:
            return 0.0

        entry_price = self._float(trade.entry_price)
        stop_loss = self._float(trade.stop_loss)
        direction = str(metadata.get("direction") or "BUY").upper()
        if direction == "SELL":
            per_share_risk = max(stop_loss - entry_price, 0.0)
        else:
            per_share_risk = max(entry_price - stop_loss, 0.0)
        return per_share_risk * remaining_shares

    def _bucket_risk_usage(self, session, signal_type: str | None, *, include_planned: bool, exclude_trade_id: str | None = None) -> float:
        bucket_key = self._bucket_key(signal_type)
        trades = session.scalars(select(PaperTrade).where(PaperTrade.exit_date.is_(None))).all()
        total = 0.0
        for trade in trades:
            if exclude_trade_id and trade.trade_id == exclude_trade_id:
                continue
            metadata = self._trade_metadata(trade)
            if self._bucket_key(trade.signal_type) != bucket_key:
                continue
            if metadata.get("plan_only") and not include_planned:
                continue
            total += self._trade_risk_amount(trade)
        return total

    def _shares_from_bucket_capacity(
        self,
        raw_shares: int,
        *,
        entry_price: float,
        leverage_multiplier: float,
        remaining_bucket_capital: float,
    ) -> int:
        if raw_shares <= 0 or remaining_bucket_capital <= 0:
            return 0
        max_notional = remaining_bucket_capital * max(leverage_multiplier, 1.0)
        max_shares = int(max_notional / max(entry_price, 0.01))
        return max(0, min(raw_shares, max_shares))

    def _estimate_intraday_margin_requirement(self, signal: dict[str, Any]) -> float:
        analysis = signal.get("analysis_snapshot") or {}
        close = self._float(analysis.get("close") or signal.get("entry_price"), default=1.0)
        atr = self._float(analysis.get("atr_14"), default=self._atr_proxy(close))
        atr_pct = atr / max(close, 0.01)
        regime = str(signal.get("regime_at_entry") or "").upper()

        margin_pct = 0.20
        if regime_is_high_volatility(regime):
            margin_pct = 0.35
        elif regime_trend_direction(regime) == "BEAR":
            margin_pct = 0.30
        elif atr_pct >= 0.03:
            margin_pct = 0.30
        elif atr_pct >= 0.02:
            margin_pct = 0.25
        return max(0.20, min(margin_pct, 1.0))

    @staticmethod
    def _holding_days_for_strategy(strategy_name: str, signal_type: str) -> int:
        if signal_type == "INTRADAY":
            return 0
        if strategy_name == "Golden Cross":
            return 240
        if strategy_name in {"Combined Regime-Aware", "Supertrend", "Breakout with Volume", "Support and Resistance", "MACD Momentum", "EMA Crossover"}:
            return 75
        return 45

    def _resolve_trade_rules(self, signal: dict[str, Any]) -> dict[str, Any]:
        signal_type = str(signal.get("signal_type") or "INTRADAY").upper()
        strategy_name = str(signal.get("strategy_name") or "")

        if signal_type == "INTRADAY":
            margin_requirement_pct = self._estimate_intraday_margin_requirement(signal)
            leverage_multiplier = min(self.INTRADAY_MARGIN_CAP, 1.0 / max(margin_requirement_pct, 0.01))
            return {
                "product_type": "INTRADAY_ROBO",
                "margin_requirement_pct": margin_requirement_pct,
                "leverage_multiplier": round(leverage_multiplier, 2),
                "max_holding_days": 0,
                "activation_window_days": 1,
                "square_off_time": self._intraday_square_off_time().isoformat(timespec="minutes"),
                "entry_deadline": self._intraday_entry_cutoff().isoformat(timespec="minutes"),
                "interest_rate_per_day": 0.0,
            }

        return {
            "product_type": "DELIVERY",
            "margin_requirement_pct": 1.0,
            "leverage_multiplier": 1.0,
            "max_holding_days": int(signal.get("max_holding_days") or self._holding_days_for_strategy(strategy_name, signal_type)),
            "activation_window_days": 5,
            "square_off_time": None,
            "entry_deadline": None,
            "interest_rate_per_day": 0.0,
        }

    def _build_trade_metadata(
        self,
        signal: dict[str, Any],
        *,
        rules: dict[str, Any],
        shares: int,
        base_shares: int,
        position_size_multiplier: float,
        entry_price: float,
        now: datetime,
        plan_only: bool,
        planned_for: date | None = None,
        activation_window_days: int | None = None,
        bucket_target_capital: float | None = None,
    ) -> dict[str, Any]:
        activation_window_days = activation_window_days or int(rules["activation_window_days"])
        entry_costs = self._entry_costs(entry_price, shares, signal["signal"])
        notional_value = entry_price * shares
        leverage_multiplier = self._float(rules["leverage_multiplier"], 1.0)
        capital_blocked = notional_value / max(leverage_multiplier, 1.0)
        borrowed_amount = max(notional_value - capital_blocked, 0.0)
        planned_for = planned_for or now.date()
        plan_expires_on = self._add_trading_days(planned_for, max(activation_window_days - 1, 0))
        bucket_key = self._bucket_key(signal.get("signal_type"))
        bucket_target_capital = bucket_target_capital if bucket_target_capital is not None else self._bucket_target_capital(signal.get("signal_type"))
        return {
            "direction": signal["signal"],
            "capital_bucket": bucket_key,
            "capital_bucket_label": self._bucket_label(bucket_key),
            "capital_bucket_allocation_pct": self._bucket_allocation_pct(signal.get("signal_type")),
            "capital_bucket_target_capital": bucket_target_capital,
            "product_type": rules["product_type"],
            "margin_requirement_pct": rules["margin_requirement_pct"],
            "leverage_multiplier": leverage_multiplier,
            "capital_blocked": capital_blocked,
            "borrowed_amount": borrowed_amount,
            "max_holding_days": rules["max_holding_days"],
            "activation_window_days": activation_window_days,
            "square_off_time": rules["square_off_time"],
            "entry_deadline": rules["entry_deadline"],
            "interest_rate_per_day": rules["interest_rate_per_day"],
            "initial_shares": shares,
            "remaining_shares": shares,
            "realized_pnl_rupees": 0.0,
            "remaining_entry_costs": entry_costs,
            "entry_costs_total": entry_costs,
            "partials": [],
            "partial_exit_plan": dict(self.TARGET_EXIT_FRACTIONS),
            "opened_from": signal.get("opened_from", "signal_engine"),
            "recommendation_reason": signal.get("recommendation_reason"),
            "basis_points": signal.get("basis_points", []),
            "explanation_sections": signal.get("explanation_sections", {}),
            "news_perspective": signal.get("news_perspective"),
            "feature_breakdown": signal.get("feature_breakdown", {}),
            "analysis_snapshot": signal.get("analysis_snapshot", {}),
            "volume_ratio": (signal.get("analysis_snapshot") or {}).get("volume_ratio"),
            "rsi_at_entry": (signal.get("analysis_snapshot") or {}).get("rsi_14"),
            "adx_at_entry": (signal.get("analysis_snapshot") or {}).get("adx"),
            "sector": signal.get("sector"),
            "sector_score": signal.get("sector_score"),
            "days_to_earnings": signal.get("days_to_earnings"),
            "event_score": signal.get("event_score"),
            "event_flags": signal.get("event_flags", []),
            "fundamental_quality_score": signal.get("fundamental_quality_score"),
            "fundamental_has_snapshot": signal.get("fundamental_has_snapshot"),
            "fundamental_confidence": signal.get("fundamental_confidence"),
            "financial_data_source": signal.get("financial_data_source"),
            "source_kind": signal.get("source_kind"),
            "audit_payload": signal.get("audit_payload", {}),
            "global_risk_level": signal.get("global_risk_level"),
            "global_risk_scan_type": signal.get("global_risk_scan_type"),
            "global_risk_as_of_date": signal.get("global_risk_as_of_date"),
            "position_size_multiplier": float(position_size_multiplier),
            "active_global_signals": list(signal.get("active_global_signals") or []),
            "global_signal_details": dict(signal.get("global_signal_details") or {}),
            "base_planned_shares": int(base_shares),
            "intelligence_notes": signal.get("intelligence_notes", []),
            "watchlist_reason": signal.get("watchlist_reason"),
            "trigger_style": signal.get("trigger_style", "ENTRY_ZONE"),
            "plan_only": plan_only,
            "plan_status": "PLANNED" if plan_only else "OPEN",
            "planned_for_date": planned_for.isoformat(),
            "plan_expires_on": plan_expires_on.isoformat(),
        }

    def _revalue_trade(self, trade: PaperTrade, latest_price: float, now: datetime) -> None:
        metadata = self._trade_metadata(trade)
        remaining_shares = int(metadata.get("remaining_shares", 0))
        initial_shares = max(int(metadata.get("initial_shares", trade.shares or 0)), 1)
        realized = self._float(metadata.get("realized_pnl_rupees"))
        remaining_entry_costs = self._float(metadata.get("remaining_entry_costs"))
        direction = str(metadata.get("direction") or "BUY")
        entry_price = self._float(trade.entry_price)

        if remaining_shares <= 0 or trade.exit_date is not None:
            total_pnl = realized
        else:
            unrealized_gross = (
                (latest_price - entry_price) * remaining_shares
                if direction == "BUY"
                else (entry_price - latest_price) * remaining_shares
            )
            estimated_exit_price = self._apply_exit_slippage(latest_price, direction)
            estimated_exit_costs = self._exit_costs(estimated_exit_price, remaining_shares, direction)
            borrowed_amount = self._float(metadata.get("borrowed_amount"))
            holding_days = max((now.date() - (trade.entry_date or now.date())).days, 0)
            interest_days = max(holding_days - 1, 0)
            interest_charge = borrowed_amount * self._float(metadata.get("interest_rate_per_day")) * interest_days
            remaining_fraction = remaining_shares / initial_shares
            total_pnl = realized + unrealized_gross - remaining_entry_costs - estimated_exit_costs - (interest_charge * remaining_fraction)

        trade.current_price = latest_price
        trade.pnl_rupees = round(total_pnl, 2)
        base_value = entry_price * initial_shares
        trade.pnl_pct = round((total_pnl / base_value) * 100, 4) if base_value else 0.0
        trade.metadata_json = metadata
        if trade.exit_date is not None:
            trade.was_profitable = bool((trade.pnl_rupees or 0.0) > 0)

    def _plan_expired(self, trade: PaperTrade, now: datetime) -> bool:
        metadata = self._trade_metadata(trade)
        expiry_value = metadata.get("plan_expires_on")
        if not expiry_value:
            return False
        expiry_day = date.fromisoformat(str(expiry_value))
        if now.date() > expiry_day:
            return True
        return trade.signal_type == "INTRADAY" and now.date() >= expiry_day and now.time() >= self._intraday_entry_cutoff()

    def _intraday_entry_deadline(self, trade: PaperTrade) -> time | None:
        metadata = self._trade_metadata(trade)
        deadline_value = metadata.get("entry_deadline")
        if not deadline_value:
            return None
        try:
            return time.fromisoformat(str(deadline_value))
        except ValueError:
            return None

    def _activation_day_reached(self, trade: PaperTrade, now: datetime) -> bool:
        metadata = self._trade_metadata(trade)
        planned_for = trade.entry_date
        if planned_for is None or planned_for <= now.date():
            return True
        return bool(metadata.get("allow_same_day_activation"))

    def _activation_window_open(self, trade: PaperTrade, now: datetime) -> bool:
        if now.time() < self._market_open_time():
            return False
        deadline = self._intraday_entry_deadline(trade)
        if deadline is not None and now.time() >= deadline:
            return False
        return self._activation_day_reached(trade, now)

    def _mark_plan_missed(self, trade: PaperTrade, now: datetime, reason: str) -> None:
        metadata = self._trade_metadata(trade)
        metadata["plan_only"] = True
        metadata["plan_status"] = "MISSED"
        trade.metadata_json = metadata
        trade.current_price = trade.current_price or trade.entry_price
        trade.exit_date = now.date()
        trade.exit_time = now.time()
        trade.exit_reason = reason
        trade.pnl_rupees = 0.0
        trade.pnl_pct = 0.0
        trade.was_profitable = False

    def _entry_triggered(self, trade: PaperTrade, latest_price: float) -> bool:
        metadata = self._trade_metadata(trade)
        direction = str(metadata.get("direction") or "BUY")
        entry_low = self._float(trade.entry_zone_low or trade.entry_price)
        entry_high = self._float(trade.entry_zone_high or trade.entry_price)
        trigger_style = str(metadata.get("trigger_style") or "ENTRY_ZONE").upper()

        if direction == "BUY":
            if trigger_style == "BREAKOUT":
                return latest_price >= entry_low and latest_price <= max(entry_high * 1.02, entry_low)
            return entry_low <= latest_price <= max(entry_high, entry_low)
        if trigger_style == "BREAKDOWN":
            return latest_price <= entry_high and latest_price >= min(entry_low * 0.98, entry_high)
        return min(entry_low, entry_high) <= latest_price <= max(entry_low, entry_high)

    def _activate_planned_trade(self, session, trade: PaperTrade, latest_price: float, now: datetime) -> None:
        metadata = self._trade_metadata(trade)
        direction = str(metadata.get("direction") or "BUY")
        leverage_multiplier = self._float(metadata.get("leverage_multiplier"), 1.0)
        atr = abs(self._float(trade.entry_price) - self._float(trade.stop_loss)) / 2 or self._atr_proxy(self._float(trade.entry_price))
        executed_entry = self._apply_entry_slippage(latest_price, direction)
        bucket_target_capital = self._bucket_target_capital(trade.signal_type, session=session)
        bucket_risk_budget = self._bucket_risk_budget(trade.signal_type, session=session)
        open_bucket_risk = self._bucket_risk_usage(session, trade.signal_type, include_planned=False, exclude_trade_id=trade.trade_id)
        remaining_bucket_risk = max(bucket_risk_budget - open_bucket_risk, 0.0)
        raw_shares = calculate_size(
            float(trade.confidence_score or 70.0),
            atr,
            bucket_target_capital,
            executed_entry,
            regime=trade.regime_at_entry,
            leverage_multiplier=leverage_multiplier,
            remaining_risk_amount=remaining_bucket_risk,
        )
        open_bucket_usage = self._bucket_usage(session, trade.signal_type, include_planned=False, exclude_trade_id=trade.trade_id)
        remaining_bucket_capital = max(bucket_target_capital - open_bucket_usage, 0.0)
        shares = self._shares_from_bucket_capacity(
            raw_shares,
            entry_price=executed_entry,
            leverage_multiplier=leverage_multiplier,
            remaining_bucket_capital=remaining_bucket_capital,
        )
        if shares <= 0:
            self._mark_plan_missed(trade, now, "RISK_LIMIT_FULL" if remaining_bucket_risk <= 0 else "ALLOCATION_FULL")
            return

        notional_value = executed_entry * shares
        capital_blocked = notional_value / max(leverage_multiplier, 1.0)
        borrowed_amount = max(notional_value - capital_blocked, 0.0)
        metadata.update(
            {
                "plan_only": False,
                "plan_status": "OPEN",
                "activated_at": now.isoformat(),
                "capital_blocked": capital_blocked,
                "borrowed_amount": borrowed_amount,
                "capital_bucket_target_capital": bucket_target_capital,
                "capital_bucket_available_before_entry": remaining_bucket_capital,
                "risk_budget_cap": bucket_risk_budget,
                "risk_budget_available_before_entry": remaining_bucket_risk,
                "risk_amount_at_entry": max(abs(self._float(trade.stop_loss) - executed_entry), 0.0) * shares,
                "initial_shares": shares,
                "remaining_shares": shares,
                "remaining_entry_costs": self._entry_costs(executed_entry, shares, direction),
                "entry_costs_total": self._entry_costs(executed_entry, shares, direction),
                "realized_pnl_rupees": 0.0,
                "partials": [],
            }
        )
        trade.metadata_json = metadata
        trade.entry_date = now.date()
        trade.entry_time = now.time()
        trade.entry_price = executed_entry
        trade.current_price = latest_price
        trade.exit_date = None
        trade.exit_time = None
        trade.exit_price = None
        trade.exit_reason = None
        trade.was_profitable = False
        trade.shares = shares
        trade.targets_hit = {"T1": False, "T2": False, "T3": False}
        self._revalue_trade(trade, latest_price, now)
        add_notification(
            session,
            notification_type="PAPER_TRADE_OPENED",
            title=f"Planned trade activated for {trade.stock_symbol}",
            body=(
                f"{trade.strategy_name} opened as {metadata['product_type']} at {executed_entry:.2f} "
                f"with {metadata['leverage_multiplier']:.2f}x effective leverage."
            ),
            color="blue",
            related_stock=trade.stock_symbol,
        )

    def open_trade(self, signal: dict[str, Any]) -> str:
        safe, reason = self.kill_switch.check_all()
        if not safe:
            self.kill_switch.activate(reason)
            raise RuntimeError(reason)

        trade_day = datetime.now(tz=settings.tzinfo).date()
        if not self.market_calendar.is_trading_day(trade_day):
            raise RuntimeError(self.market_calendar.closure_reason(trade_day) or "Market is closed.")

        rules = self._resolve_trade_rules(signal)
        direction = str(signal["signal"])
        entry_price = self._apply_entry_slippage(self._float(signal["entry_price"]), direction)
        atr = abs(self._float(signal["entry_price"]) - self._float(signal["stop_loss"])) / 2 or self._float(signal["entry_price"]) * 0.02
        now = datetime.now(tz=settings.tzinfo)

        with session_scope() as session:
            bucket_target_capital = self._bucket_target_capital(signal.get("signal_type"), session=session)
            bucket_risk_budget = self._bucket_risk_budget(signal.get("signal_type"), session=session)
            leverage_multiplier = self._float(rules["leverage_multiplier"], 1.0)
            open_bucket_risk = self._bucket_risk_usage(session, signal.get("signal_type"), include_planned=False)
            remaining_bucket_risk = max(bucket_risk_budget - open_bucket_risk, 0.0)
            raw_shares = calculate_size(
                float(signal["confidence_score"]),
                atr,
                bucket_target_capital,
                entry_price,
                regime=signal.get("regime_at_entry"),
                leverage_multiplier=leverage_multiplier,
                remaining_risk_amount=remaining_bucket_risk,
            )
            open_bucket_usage = self._bucket_usage(session, signal.get("signal_type"), include_planned=False)
            remaining_bucket_capital = max(bucket_target_capital - open_bucket_usage, 0.0)
            shares = self._shares_from_bucket_capacity(
                raw_shares,
                entry_price=entry_price,
                leverage_multiplier=leverage_multiplier,
                remaining_bucket_capital=remaining_bucket_capital,
            )
            if shares <= 0:
                if remaining_bucket_risk <= 0:
                    raise RuntimeError(f"{self._bucket_label(self._bucket_key(signal.get('signal_type')))} paper-trade risk budget is fully allocated.")
                raise RuntimeError(f"{self._bucket_label(self._bucket_key(signal.get('signal_type')))} paper-trade capital is fully allocated.")

            metadata = self._build_trade_metadata(
                signal,
                rules=rules,
                shares=shares,
                base_shares=shares,
                position_size_multiplier=1.0,
                entry_price=entry_price,
                now=now,
                plan_only=False,
                bucket_target_capital=bucket_target_capital,
            )
            metadata["capital_bucket_available_before_entry"] = remaining_bucket_capital
            metadata["risk_budget_cap"] = bucket_risk_budget
            metadata["risk_budget_available_before_entry"] = remaining_bucket_risk
            metadata["risk_amount_at_entry"] = max(abs(self._float(signal["stop_loss"]) - entry_price), 0.0) * shares
            trade = PaperTrade(
                stock_symbol=signal["stock_symbol"],
                strategy_name=signal["strategy_name"],
                signal_type=signal["signal_type"],
                entry_date=now.date(),
                entry_time=now.time(),
                entry_price=entry_price,
                entry_zone_low=signal.get("entry_zone_low"),
                entry_zone_high=signal.get("entry_zone_high"),
                stop_loss=signal["stop_loss"],
                target_1=signal["target_1"],
                target_2=signal["target_2"],
                target_3=signal["target_3"],
                shares=shares,
                confidence_score=signal["confidence_score"],
                regime_at_entry=signal.get("regime_at_entry"),
                news_score_at_entry=signal.get("news_score_at_entry"),
                pattern_name=signal.get("pattern_name"),
                current_price=self._float(signal["entry_price"]),
                targets_hit={"T1": False, "T2": False, "T3": False},
                metadata_json=metadata,
            )
            session.add(trade)
            session.flush()
            self._revalue_trade(trade, self._float(signal["entry_price"]), now)
            add_notification(
                session,
                notification_type="PAPER_TRADE_OPENED",
                title=f"Paper trade opened for {trade.stock_symbol}",
                body=(
                    f"{trade.strategy_name} opened a {direction} {metadata['product_type']} paper trade at {entry_price:.2f} "
                    f"with {metadata['leverage_multiplier']:.2f}x leverage."
                ),
                color="blue",
                related_stock=trade.stock_symbol,
            )
            trade_id = trade.trade_id
        return trade_id

    def plan_signal_trade(
        self,
        signal: dict[str, Any],
        *,
        planned_for: date | None = None,
        activation_window_days: int | None = None,
        position_size_multiplier: float = 1.0,
    ) -> str:
        planned_for = planned_for or self.market_calendar.next_trading_day(datetime.now(tz=settings.tzinfo).date())
        rules = self._resolve_trade_rules(signal)
        entry_price = self._float(signal["entry_price"])
        atr = abs(entry_price - self._float(signal["stop_loss"])) / 2 or self._atr_proxy(entry_price)
        now = datetime.now(tz=settings.tzinfo)
        position_size_multiplier = max(float(position_size_multiplier), 0.0)
        if position_size_multiplier <= 0.0:
            raise ValueError("position_size_multiplier must be greater than 0 for planned trades")

        with session_scope() as session:
            bucket_target_capital = self._bucket_target_capital(signal.get("signal_type"), session=session)
            leverage_multiplier = self._float(rules["leverage_multiplier"], 1.0)
            base_shares = max(
                calculate_size(
                    float(signal.get("confidence_score", 70.0)),
                    atr,
                    bucket_target_capital,
                    entry_price,
                    regime=signal.get("regime_at_entry"),
                    leverage_multiplier=leverage_multiplier,
                ),
                1,
            )
            shares = max(int(round(base_shares * position_size_multiplier)), 1)
            metadata = self._build_trade_metadata(
                signal,
                rules=rules,
                shares=shares,
                base_shares=base_shares,
                position_size_multiplier=position_size_multiplier,
                entry_price=entry_price,
                now=now,
                plan_only=True,
                planned_for=planned_for,
                activation_window_days=activation_window_days,
                bucket_target_capital=bucket_target_capital,
            )
            metadata["capital_bucket_reserved_at_plan"] = self._bucket_usage(session, signal.get("signal_type"), include_planned=True)
            existing = next(
                (
                    trade
                    for trade in session.scalars(
                        select(PaperTrade).where(
                            PaperTrade.stock_symbol == signal["stock_symbol"],
                            PaperTrade.exit_date.is_(None),
                        )
                    ).all()
                    if (trade.metadata_json or {}).get("plan_only")
                ),
                None,
            )
            if existing is not None:
                existing.strategy_name = signal["strategy_name"]
                existing.signal_type = signal["signal_type"]
                existing.entry_date = planned_for
                existing.entry_time = self._market_open_time()
                existing.entry_price = entry_price
                existing.entry_zone_low = signal.get("entry_zone_low")
                existing.entry_zone_high = signal.get("entry_zone_high")
                existing.stop_loss = signal["stop_loss"]
                existing.target_1 = signal["target_1"]
                existing.target_2 = signal["target_2"]
                existing.target_3 = signal["target_3"]
                existing.shares = shares
                existing.confidence_score = float(signal.get("confidence_score", 70.0))
                existing.regime_at_entry = signal.get("regime_at_entry")
                existing.news_score_at_entry = signal.get("news_score_at_entry")
                existing.pattern_name = signal.get("pattern_name")
                existing.current_price = entry_price
                existing.targets_hit = {"T1": False, "T2": False, "T3": False}
                existing.metadata_json = metadata
                return existing.trade_id

            trade = PaperTrade(
                stock_symbol=signal["stock_symbol"],
                strategy_name=signal["strategy_name"],
                signal_type=signal["signal_type"],
                entry_date=planned_for,
                entry_time=self._market_open_time(),
                entry_price=entry_price,
                entry_zone_low=signal.get("entry_zone_low"),
                entry_zone_high=signal.get("entry_zone_high"),
                stop_loss=signal["stop_loss"],
                target_1=signal["target_1"],
                target_2=signal["target_2"],
                target_3=signal["target_3"],
                shares=shares,
                confidence_score=float(signal.get("confidence_score", 70.0)),
                regime_at_entry=signal.get("regime_at_entry"),
                news_score_at_entry=signal.get("news_score_at_entry"),
                pattern_name=signal.get("pattern_name"),
                current_price=entry_price,
                targets_hit={"T1": False, "T2": False, "T3": False},
                metadata_json=metadata,
            )
            session.add(trade)
            session.flush()
            return trade.trade_id

    def plan_watchlist_trade(
        self,
        *,
        stock_symbol: str,
        watch_price: float,
        reason: str,
        strategy_name: str | None = None,
        signal_type: str = "INTRADAY",
        direction: str = "BUY",
        planned_for: date | None = None,
        trigger_price: float | None = None,
        trigger_style: str = "BREAKOUT",
        confidence_score: float = 70.0,
        news_score: float = 0.0,
        news_perspective: str | None = None,
        event_flags: list[str] | None = None,
        basis_points: list[str] | None = None,
        explanation_sections: dict[str, list[str]] | None = None,
        sector: str | None = None,
        sector_score: float | None = None,
        fundamental_quality_score: float | None = None,
        fundamental_has_snapshot: bool | None = None,
        fundamental_confidence: float | None = None,
        financial_data_source: str | None = None,
    ) -> str:
        planned_for = planned_for or self.market_calendar.next_trading_day(datetime.now(tz=settings.tzinfo).date())
        trigger_price = trigger_price or watch_price
        direction = direction.upper()
        stop_loss, target_1, target_2, target_3 = self._plan_levels(trigger_price, direction)
        signal = {
            "stock_symbol": stock_symbol,
            "strategy_name": strategy_name or "Tomorrow Watchlist",
            "signal_type": signal_type,
            "signal": direction,
            "entry_price": round(trigger_price, 2),
            "entry_zone_low": round(trigger_price * (0.999 if signal_type == "INTRADAY" else 0.995), 2),
            "entry_zone_high": round(trigger_price * (1.003 if signal_type == "INTRADAY" else 1.01), 2),
            "stop_loss": round(stop_loss, 2),
            "target_1": round(target_1, 2),
            "target_2": round(target_2, 2),
            "target_3": round(target_3, 2),
            "confidence_score": float(confidence_score),
            "pattern_name": "watchlist_plan",
            "opened_from": "after_market_watchlist",
            "recommendation_reason": reason,
            "basis_points": list(basis_points or [reason]),
            "explanation_sections": explanation_sections or {},
            "watchlist_reason": reason,
            "trigger_style": trigger_style,
            "news_score_at_entry": float(news_score),
            "event_flags": list(event_flags or []),
            "news_perspective": news_perspective,
            "sector": sector,
            "sector_score": sector_score,
            "fundamental_quality_score": fundamental_quality_score,
            "fundamental_has_snapshot": fundamental_has_snapshot,
            "fundamental_confidence": fundamental_confidence,
            "financial_data_source": financial_data_source,
            "allow_same_day_activation": True,
        }
        return self.plan_signal_trade(signal, planned_for=planned_for, activation_window_days=1 if signal_type == "INTRADAY" else 5)

    def clear_planned_watchlist_trades(
        self,
        *,
        from_date: date | None = None,
        signal_type: str | None = None,
    ) -> None:
        from_date = from_date or datetime.now(tz=settings.tzinfo).date()
        with session_scope() as session:
            trades = session.scalars(
                select(PaperTrade).where(
                    PaperTrade.entry_date >= from_date,
                    PaperTrade.exit_date.is_(None),
                )
            ).all()
            for trade in trades:
                if signal_type is not None and str(trade.signal_type or "").upper() != str(signal_type).upper():
                    continue
                if (trade.metadata_json or {}).get("plan_only"):
                    session.delete(trade)

    def activate_planned_trades(self, latest_prices: dict[str, float], *, now: datetime | None = None) -> dict[str, int]:
        safe, reason = self.kill_switch.check_all()
        if not safe:
            self.kill_switch.activate(reason)
            return {"activated": 0, "expired": 0}

        now = now or datetime.now(tz=settings.tzinfo)
        activated = 0
        expired = 0
        with session_scope() as session:
            trades = session.scalars(select(PaperTrade).where(PaperTrade.exit_date.is_(None))).all()
            for trade in trades:
                metadata = self._trade_metadata(trade)
                if not metadata.get("plan_only") or not trade.stock_symbol:
                    continue
                if not self._activation_window_open(trade, now):
                    continue
                price = latest_prices.get(trade.stock_symbol)
                if price is None:
                    continue
                if self._plan_expired(trade, now):
                    self._mark_plan_missed(trade, now, "PLAN_EXPIRED")
                    expired += 1
                    continue
                if self._entry_triggered(trade, price):
                    self._activate_planned_trade(session, trade, price, now)
                    activated += 1
        return {"activated": activated, "expired": expired}

    def _process_open_trade(self, session, trade: PaperTrade, price: float, now: datetime) -> None:
        metadata = self._trade_metadata(trade)
        if metadata.get("plan_only") or not trade.stock_symbol:
            return

        self._revalue_trade(trade, price, now)
        direction = str(metadata.get("direction") or "BUY")
        targets_hit = dict(trade.targets_hit or {"T1": False, "T2": False, "T3": False})

        if self._margin_protection_hit(trade):
            self._close_remaining_in_session(session, trade, price, "MARGIN_PROTECT", now)
            return

        max_holding_days = int(metadata.get("max_holding_days") or 0)
        if trade.signal_type == "INTRADAY" and now.time() >= self._intraday_square_off_time():
            self._close_remaining_in_session(session, trade, price, "AUTO_SQUARE_OFF", now)
            return
        if trade.signal_type != "INTRADAY" and max_holding_days > 0 and trade.entry_date is not None:
            if (now.date() - trade.entry_date).days >= max_holding_days:
                self._close_remaining_in_session(session, trade, price, "MAX_HOLD_REACHED", now)
                return

        if direction == "BUY":
            if self._float(price) <= self._float(trade.stop_loss):
                self._close_remaining_in_session(session, trade, price, "STOP_HIT", now)
                return
            if self._float(price) >= self._float(trade.target_1) and not targets_hit["T1"]:
                self._execute_exit(trade, price, now, "TARGET_1", shares_to_exit=self._partial_shares(trade, "T1"))
                targets_hit["T1"] = True
                trade.stop_loss = max(self._float(trade.stop_loss), self._float(trade.entry_price))
                add_notification(
                    session,
                    notification_type="TARGET",
                    title=f"{trade.stock_symbol} hit Target 1",
                    body="Booked partial profit and moved stop to breakeven.",
                    color="green",
                    related_stock=trade.stock_symbol,
                )
            if trade.exit_date is not None:
                trade.targets_hit = targets_hit
                return
            if self._float(price) >= self._float(trade.target_2) and not targets_hit["T2"]:
                self._execute_exit(trade, price, now, "TARGET_2", shares_to_exit=self._partial_shares(trade, "T2"))
                targets_hit["T2"] = True
                trade.stop_loss = max(self._float(trade.stop_loss), self._float(trade.target_1) or self._float(trade.entry_price))
                add_notification(
                    session,
                    notification_type="TARGET",
                    title=f"{trade.stock_symbol} hit Target 2",
                    body="Booked additional partial profit and tightened the stop.",
                    color="green",
                    related_stock=trade.stock_symbol,
                )
            if trade.exit_date is not None:
                trade.targets_hit = targets_hit
                return
            if trade.target_3 and self._float(price) >= self._float(trade.target_3):
                targets_hit["T3"] = True
                self._close_remaining_in_session(session, trade, price, "TARGET_3", now)
                trade.targets_hit = targets_hit
                return
        else:
            if self._float(price) >= self._float(trade.stop_loss):
                self._close_remaining_in_session(session, trade, price, "STOP_HIT", now)
                return
            if self._float(price) <= self._float(trade.target_1) and not targets_hit["T1"]:
                self._execute_exit(trade, price, now, "TARGET_1", shares_to_exit=self._partial_shares(trade, "T1"))
                targets_hit["T1"] = True
                trade.stop_loss = min(self._float(trade.stop_loss), self._float(trade.entry_price))
                add_notification(
                    session,
                    notification_type="TARGET",
                    title=f"{trade.stock_symbol} hit Target 1",
                    body="Booked partial profit and moved stop to breakeven.",
                    color="green",
                    related_stock=trade.stock_symbol,
                )
            if trade.exit_date is not None:
                trade.targets_hit = targets_hit
                return
            if self._float(price) <= self._float(trade.target_2) and not targets_hit["T2"]:
                self._execute_exit(trade, price, now, "TARGET_2", shares_to_exit=self._partial_shares(trade, "T2"))
                targets_hit["T2"] = True
                trade.stop_loss = min(self._float(trade.stop_loss), self._float(trade.target_1) or self._float(trade.entry_price))
                add_notification(
                    session,
                    notification_type="TARGET",
                    title=f"{trade.stock_symbol} hit Target 2",
                    body="Booked additional partial profit and tightened the stop.",
                    color="green",
                    related_stock=trade.stock_symbol,
                )
            if trade.exit_date is not None:
                trade.targets_hit = targets_hit
                return
            if trade.target_3 and self._float(price) <= self._float(trade.target_3):
                targets_hit["T3"] = True
                self._close_remaining_in_session(session, trade, price, "TARGET_3", now)
                trade.targets_hit = targets_hit
                return

        trade.targets_hit = targets_hit
        self._revalue_trade(trade, price, now)

    def process_realtime_price(self, symbol: str, latest_price: float, *, now: datetime | None = None) -> dict[str, int]:
        safe, reason = self.kill_switch.check_all()
        if not safe:
            self.kill_switch.activate(reason)
            return {"activated": 0, "updated": 0, "expired": 0}

        now = now or datetime.now(tz=settings.tzinfo)
        symbol = symbol.upper()
        activated = 0
        updated = 0
        expired = 0

        with session_scope() as session:
            trades = session.scalars(
                select(PaperTrade).where(
                    PaperTrade.stock_symbol == symbol,
                    PaperTrade.exit_date.is_(None),
                )
            ).all()
            for trade in trades:
                metadata = self._trade_metadata(trade)
                if metadata.get("plan_only"):
                    if not self._activation_window_open(trade, now):
                        continue
                    if self._plan_expired(trade, now):
                        self._mark_plan_missed(trade, now, "PLAN_EXPIRED")
                        expired += 1
                        continue
                    if self._entry_triggered(trade, latest_price):
                        self._activate_planned_trade(session, trade, latest_price, now)
                        activated += 1
                    continue

                self._process_open_trade(session, trade, latest_price, now)
                updated += 1

        return {"activated": activated, "updated": updated, "expired": expired}

    def _margin_protection_hit(self, trade: PaperTrade) -> bool:
        metadata = self._trade_metadata(trade)
        if metadata.get("product_type") != "INTRADAY_ROBO":
            return False
        capital_blocked = self._float(metadata.get("capital_blocked"))
        return capital_blocked > 0 and self._float(trade.pnl_rupees) <= -(capital_blocked * 0.80)

    def _partial_shares(self, trade: PaperTrade, target_key: str) -> int:
        metadata = self._trade_metadata(trade)
        initial_shares = max(int(metadata.get("initial_shares", trade.shares or 0)), 1)
        remaining_shares = int(metadata.get("remaining_shares", trade.shares or 0))
        fraction = self._float((metadata.get("partial_exit_plan") or {}).get(target_key), self.TARGET_EXIT_FRACTIONS[target_key])
        if target_key == "T3":
            return remaining_shares
        return max(1, min(remaining_shares, int(round(initial_shares * fraction))))

    def _execute_exit(self, trade: PaperTrade, exit_price: float, now: datetime, reason: str, *, shares_to_exit: int) -> None:
        metadata = self._trade_metadata(trade)
        remaining_before = int(metadata.get("remaining_shares", trade.shares or 0))
        if remaining_before <= 0:
            return
        shares_to_exit = max(1, min(shares_to_exit, remaining_before))
        direction = str(metadata.get("direction") or "BUY")
        executed_exit = self._apply_exit_slippage(exit_price, direction)
        gross_pnl = (
            (executed_exit - self._float(trade.entry_price)) * shares_to_exit
            if direction == "BUY"
            else (self._float(trade.entry_price) - executed_exit) * shares_to_exit
        )
        remaining_entry_costs = self._float(metadata.get("remaining_entry_costs"))
        entry_cost_alloc = remaining_entry_costs * (shares_to_exit / remaining_before)
        exit_costs = self._exit_costs(executed_exit, shares_to_exit, direction)
        borrowed_amount = self._float(metadata.get("borrowed_amount"))
        holding_days = max((now.date() - (trade.entry_date or now.date())).days, 0)
        interest_days = max(holding_days - 1, 0)
        borrow_cost = borrowed_amount * self._float(metadata.get("interest_rate_per_day")) * interest_days * (
            shares_to_exit / max(int(metadata.get("initial_shares", remaining_before)), 1)
        )
        realized_increment = gross_pnl - entry_cost_alloc - exit_costs - borrow_cost

        metadata["remaining_shares"] = remaining_before - shares_to_exit
        metadata["remaining_entry_costs"] = max(remaining_entry_costs - entry_cost_alloc, 0.0)
        metadata["realized_pnl_rupees"] = self._float(metadata.get("realized_pnl_rupees")) + realized_increment
        metadata["partials"] = list(metadata.get("partials") or []) + [
            {
                "timestamp": now.isoformat(),
                "reason": reason,
                "shares": shares_to_exit,
                "exit_price": round(executed_exit, 2),
                "pnl_rupees": round(realized_increment, 2),
            }
        ]
        trade.metadata_json = metadata

        if metadata["remaining_shares"] <= 0:
            trade.exit_price = executed_exit
            trade.exit_date = now.date()
            trade.exit_time = now.time()
            trade.exit_reason = reason
            trade.was_profitable = bool(self._float(metadata.get("realized_pnl_rupees")) > 0)

        self._revalue_trade(trade, exit_price, now)

    def _close_remaining_in_session(self, session, trade: PaperTrade, exit_price: float, reason: str, now: datetime) -> None:
        metadata = self._trade_metadata(trade)
        remaining_shares = int(metadata.get("remaining_shares", trade.shares or 0))
        if remaining_shares <= 0:
            return
        self._execute_exit(trade, exit_price, now, reason, shares_to_exit=remaining_shares)
        add_notification(
            session,
            notification_type="PAPER_TRADE_CLOSED",
            title=f"Paper trade closed for {trade.stock_symbol}",
            body=f"{reason} at {self._float(trade.exit_price):.2f}. P&L: Rs {self._float(trade.pnl_rupees):.2f}.",
            color="green" if self._float(trade.pnl_rupees) > 0 else "red",
            related_stock=trade.stock_symbol,
        )
        self.learn_from_result(
            {
                "trade_id": trade.trade_id,
                "stock_symbol": trade.stock_symbol,
                "strategy_name": trade.strategy_name,
                "regime_at_entry": trade.regime_at_entry,
                "news_score_at_entry": trade.news_score_at_entry,
                "pattern_name": trade.pattern_name,
                "pnl_pct": trade.pnl_pct,
                "pnl_rupees": trade.pnl_rupees,
                "entry_time": trade.entry_time.isoformat() if trade.entry_time else None,
                "direction": metadata.get("direction"),
                "product_type": metadata.get("product_type"),
                "source_kind": metadata.get("opened_from"),
            },
            session=session,
        )

    def update_trades(self, latest_prices: dict[str, float]) -> None:
        safe, reason = self.kill_switch.check_all()
        if not safe:
            self.kill_switch.activate(reason)
            return

        now = datetime.now(tz=settings.tzinfo)
        self.activate_planned_trades(latest_prices, now=now)

        with session_scope() as session:
            trades = session.scalars(select(PaperTrade).where(PaperTrade.exit_date.is_(None))).all()
            for trade in trades:
                metadata = self._trade_metadata(trade)
                if metadata.get("plan_only") or not trade.stock_symbol:
                    continue
                price = latest_prices.get(trade.stock_symbol)
                if price is None:
                    continue
                self._process_open_trade(session, trade, price, now)

    def close_trade(self, trade_id: str, exit_price: float, reason: str) -> None:
        safe, kill_reason = self.kill_switch.check_all()
        if not safe and reason != "KILL_SWITCH":
            self.kill_switch.activate(kill_reason)
            return
        with session_scope() as session:
            trade = session.get(PaperTrade, trade_id)
            if trade is None or trade.exit_date is not None:
                return
            self._close_remaining_in_session(session, trade, exit_price, reason, datetime.now(tz=settings.tzinfo))

    def learn_from_result(self, trade: dict[str, Any], session=None):
        if session is None:
            with session_scope() as managed_session:
                self.learn_from_result(trade, session=managed_session)
            return

        pnl_pct = self._float(trade.get("pnl_pct"))
        pnl_rupees = self._float(trade.get("pnl_rupees"))
        if pnl_pct < 0 or pnl_rupees < 0:
            existing = session.scalar(select(MistakeLog).where(MistakeLog.trade_id == trade["trade_id"]))
            if existing is not None:
                return
            adjustment = "Reduce confidence by 10% for similar setups and require stronger confirmation."
            if pnl_pct <= -1.5:
                adjustment = "Reduce confidence by 20% for similar setups in comparable conditions."
            if trade.get("product_type") == "INTRADAY_ROBO":
                adjustment = "Reduce confidence by 10% and tighten intraday entry filters for similar setups."
            if regime_is_high_volatility(trade.get("regime_at_entry")):
                adjustment = "Reduce confidence by 20% for breakout signals during HIGH_VOLATILITY."
            session.add(
                MistakeLog(
                    trade_id=trade["trade_id"],
                    conditions_at_loss={
                        "pattern": trade.get("pattern_name"),
                        "regime": trade.get("regime_at_entry"),
                        "newsScore": trade.get("news_score_at_entry"),
                        "timeOfDay": trade.get("entry_time"),
                        "direction": trade.get("direction"),
                        "productType": trade.get("product_type"),
                        "sourceKind": trade.get("source_kind"),
                        "pnlPct": pnl_pct,
                    },
                    adjustment_made=adjustment,
                )
            )
