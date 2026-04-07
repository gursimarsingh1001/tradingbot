from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select

from backend.config import get_settings
from backend.db.postgres import MistakeLog, PaperTrade, add_notification, session_scope
from backend.engine.kill_switch import KillSwitch
from backend.engine.market_calendar import get_market_calendar
from backend.engine.regime_detector import regime_is_high_volatility
from backend.engine.paper_trader_v2 import PaperTrader as PaperTraderV2
from backend.engine.position_sizer import calculate_size


settings = get_settings()


class _LegacyPaperTrader:
    def __init__(self, kill_switch: KillSwitch | None = None) -> None:
        self.kill_switch = kill_switch or KillSwitch()
        self.market_calendar = get_market_calendar()

    @staticmethod
    def _apply_entry_slippage(price: float, direction: str) -> float:
        return price * (1.001 if direction == "BUY" else 0.999)

    @staticmethod
    def _apply_exit_slippage(price: float, direction: str) -> float:
        return price * (0.999 if direction == "BUY" else 1.001)

    @staticmethod
    def _transaction_costs(entry_price: float, exit_price: float, shares: int) -> float:
        turnover_entry = entry_price * shares
        turnover_exit = exit_price * shares
        brokerage = 40.0
        stt = turnover_exit * 0.001
        exchange_charge = (turnover_entry + turnover_exit) * 0.0005
        slippage = turnover_entry * 0.001 + turnover_exit * 0.001
        return brokerage + stt + exchange_charge + slippage

    @staticmethod
    def _atr_proxy(entry_price: float) -> float:
        return max(entry_price * 0.01, 1.0)

    @staticmethod
    def _plan_levels(entry_price: float, direction: str = "BUY") -> tuple[float, float, float, float]:
        atr_proxy = _LegacyPaperTrader._atr_proxy(entry_price)
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

    def open_trade(self, signal: dict[str, Any]) -> str:
        safe, reason = self.kill_switch.check_all()
        if not safe:
            self.kill_switch.activate(reason)
            raise RuntimeError(reason)

        trade_day = datetime.now(tz=settings.tzinfo).date()
        if not self.market_calendar.is_trading_day(trade_day):
            raise RuntimeError(self.market_calendar.closure_reason(trade_day) or "Market is closed.")

        direction = signal["signal"]
        entry_price = self._apply_entry_slippage(float(signal["entry_price"]), direction)
        atr = abs(signal["entry_price"] - signal["stop_loss"]) / 2 or signal["entry_price"] * 0.02
        shares = calculate_size(
            signal["confidence_score"],
            atr,
            signal.get("portfolio_value", settings.paper_portfolio_value),
            entry_price,
            regime=signal.get("regime_at_entry"),
        )
        if shares <= 0:
            raise RuntimeError("Calculated position size is zero.")

        now = datetime.now(tz=settings.tzinfo)
        with session_scope() as session:
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
                current_price=entry_price,
                targets_hit={"T1": False, "T2": False, "T3": False},
                metadata_json={
                    "direction": direction,
                    "max_holding_days": signal.get("max_holding_days", 45 if signal["signal_type"] == "INTRADAY" else 120),
                    "opened_from": signal.get("opened_from", "signal_engine"),
                    "recommendation_reason": signal.get("recommendation_reason"),
                    "basis_points": signal.get("basis_points", []),
                    "feature_breakdown": signal.get("feature_breakdown", {}),
                    "analysis_snapshot": signal.get("analysis_snapshot", {}),
                },
            )
            session.add(trade)
            session.flush()
            add_notification(
                session,
                notification_type="PAPER_TRADE_OPENED",
                title=f"Paper trade opened for {trade.stock_symbol}",
                body=f"{trade.strategy_name} opened a {direction} paper trade at {entry_price:.2f}.",
                color="blue",
                related_stock=trade.stock_symbol,
            )
            trade_id = trade.trade_id
        return trade_id

    def plan_watchlist_trade(
        self,
        *,
        stock_symbol: str,
        watch_price: float,
        reason: str,
        strategy_name: str | None = None,
        signal_type: str = "INTRADAY",
        planned_for: date | None = None,
    ) -> str:
        planned_for = planned_for or self.market_calendar.next_trading_day(datetime.now(tz=settings.tzinfo).date())
        stop_loss, target_1, target_2, target_3 = self._plan_levels(watch_price)
        atr_proxy = self._atr_proxy(watch_price)
        shares = calculate_size(
            70.0,
            atr_proxy,
            settings.paper_portfolio_value,
            watch_price,
        )

        with session_scope() as session:
            existing = next(
                (
                    trade
                    for trade in session.scalars(
                        select(PaperTrade).where(
                            PaperTrade.stock_symbol == stock_symbol,
                            PaperTrade.exit_date.is_(None),
                        )
                    ).all()
                    if (trade.metadata_json or {}).get("plan_only")
                ),
                None,
            )
            if existing is not None:
                existing.strategy_name = strategy_name or "Tomorrow Watchlist"
                existing.entry_date = planned_for
                existing.entry_time = time(hour=9, minute=15)
                existing.entry_price = watch_price
                existing.entry_zone_low = round(watch_price * 0.9975, 2)
                existing.entry_zone_high = round(watch_price * 1.0025, 2)
                existing.stop_loss = round(stop_loss, 2)
                existing.target_1 = round(target_1, 2)
                existing.target_2 = round(target_2, 2)
                existing.target_3 = round(target_3, 2)
                existing.current_price = watch_price
                existing.metadata_json = {
                    **(existing.metadata_json or {}),
                    "plan_only": True,
                    "plan_status": "PLANNED",
                    "opened_from": "after_market_watchlist",
                    "watchlist_reason": reason,
                    "planned_for_date": planned_for.isoformat(),
                }
                return existing.trade_id

            trade = PaperTrade(
                stock_symbol=stock_symbol,
                strategy_name=strategy_name or "Tomorrow Watchlist",
                signal_type=signal_type,
                entry_date=planned_for,
                entry_time=time(hour=9, minute=15),
                entry_price=watch_price,
                entry_zone_low=round(watch_price * 0.9975, 2),
                entry_zone_high=round(watch_price * 1.0025, 2),
                stop_loss=round(stop_loss, 2),
                target_1=round(target_1, 2),
                target_2=round(target_2, 2),
                target_3=round(target_3, 2),
                shares=max(shares, 1),
                pnl_rupees=0.0,
                pnl_pct=0.0,
                confidence_score=70.0,
                pattern_name="watchlist_plan",
                current_price=watch_price,
                targets_hit={"T1": False, "T2": False, "T3": False},
                metadata_json={
                    "direction": "BUY",
                    "plan_only": True,
                    "plan_status": "PLANNED",
                    "opened_from": "after_market_watchlist",
                    "watchlist_reason": reason,
                    "planned_for_date": planned_for.isoformat(),
                },
            )
            session.add(trade)
            session.flush()
            return trade.trade_id

    def clear_planned_watchlist_trades(self, *, from_date: date | None = None) -> None:
        from_date = from_date or datetime.now(tz=settings.tzinfo).date()
        with session_scope() as session:
            trades = session.scalars(
                select(PaperTrade).where(
                    PaperTrade.entry_date >= from_date,
                    PaperTrade.exit_date.is_(None),
                )
            ).all()
            for trade in trades:
                if (trade.metadata_json or {}).get("plan_only"):
                    session.delete(trade)

    def update_trades(self, latest_prices: dict[str, float]) -> None:
        safe, reason = self.kill_switch.check_all()
        if not safe:
            self.kill_switch.activate(reason)
            return

        now = datetime.now(tz=settings.tzinfo)
        with session_scope() as session:
            trades = session.scalars(select(PaperTrade).where(PaperTrade.exit_date.is_(None))).all()
            for trade in trades:
                if (trade.metadata_json or {}).get("plan_only"):
                    continue
                price = latest_prices.get(trade.stock_symbol)
                if price is None:
                    continue
                trade.current_price = price
                direction = trade.metadata_json.get("direction", "BUY")
                targets_hit = dict(trade.targets_hit or {"T1": False, "T2": False, "T3": False})

                if direction == "BUY":
                    if price <= trade.stop_loss:
                        self._close_trade_in_session(session, trade, price, "STOP_HIT", now)
                        continue
                    if price >= trade.target_1 and not targets_hit["T1"]:
                        targets_hit["T1"] = True
                        trade.stop_loss = trade.entry_price
                        add_notification(
                            session,
                            notification_type="TARGET",
                            title=f"{trade.stock_symbol} hit Target 1",
                            body="Stop moved to breakeven after first target.",
                            color="green",
                            related_stock=trade.stock_symbol,
                        )
                    if price >= trade.target_2 and not targets_hit["T2"]:
                        targets_hit["T2"] = True
                    if trade.target_3 and price >= trade.target_3:
                        targets_hit["T3"] = True
                        self._close_trade_in_session(session, trade, price, "TARGET_3", now)
                        continue
                else:
                    if price >= trade.stop_loss:
                        self._close_trade_in_session(session, trade, price, "STOP_HIT", now)
                        continue
                    if price <= trade.target_1 and not targets_hit["T1"]:
                        targets_hit["T1"] = True
                        trade.stop_loss = trade.entry_price
                    if price <= trade.target_2 and not targets_hit["T2"]:
                        targets_hit["T2"] = True
                    if trade.target_3 and price <= trade.target_3:
                        targets_hit["T3"] = True
                        self._close_trade_in_session(session, trade, price, "TARGET_3", now)
                        continue

                trade.targets_hit = targets_hit

                if trade.signal_type == "INTRADAY" and now.strftime("%H:%M") >= "15:20":
                    self._close_trade_in_session(session, trade, price, "EOD_CLOSE", now)

    def close_trade(self, trade_id: str, exit_price: float, reason: str) -> None:
        safe, kill_reason = self.kill_switch.check_all()
        if not safe and reason != "KILL_SWITCH":
            self.kill_switch.activate(kill_reason)
            return
        with session_scope() as session:
            trade = session.get(PaperTrade, trade_id)
            if trade is None or trade.exit_date is not None:
                return
            self._close_trade_in_session(session, trade, exit_price, reason, datetime.now(tz=settings.tzinfo))

    def _close_trade_in_session(self, session, trade: PaperTrade, exit_price: float, reason: str, now: datetime) -> None:
        direction = trade.metadata_json.get("direction", "BUY")
        executed_exit = self._apply_exit_slippage(exit_price, direction)
        trade.exit_price = executed_exit
        trade.exit_date = now.date()
        trade.exit_time = now.time()
        trade.exit_reason = reason

        gross_pnl = (
            (executed_exit - trade.entry_price) * trade.shares
            if direction == "BUY"
            else (trade.entry_price - executed_exit) * trade.shares
        )
        costs = self._transaction_costs(trade.entry_price, executed_exit, trade.shares)
        trade.pnl_rupees = gross_pnl - costs
        trade.pnl_pct = (trade.pnl_rupees / (trade.entry_price * trade.shares)) * 100 if trade.shares else 0
        trade.was_profitable = bool(trade.pnl_rupees > 0)

        add_notification(
            session,
            notification_type="PAPER_TRADE_CLOSED",
            title=f"Paper trade closed for {trade.stock_symbol}",
            body=f"{reason} at {executed_exit:.2f}. P&L: ₹{trade.pnl_rupees:.2f}.",
            color="green" if trade.pnl_rupees > 0 else "red",
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
                "entry_time": trade.entry_time.isoformat() if trade.entry_time else None,
            },
            session=session,
        )

    def learn_from_result(self, trade: dict[str, Any], session=None):
        if session is None:
            with session_scope() as managed_session:
                self.learn_from_result(trade, session=managed_session)
            return

        if (trade.get("pnl_pct") or 0) <= -1.5:
            adjustment = "Reduce confidence by 20% for similar setups in comparable conditions."
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
                    },
                    adjustment_made=adjustment,
                )
            )


PaperTrader = PaperTraderV2

__all__ = ["PaperTrader"]
