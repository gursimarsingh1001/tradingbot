from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import select

from backend.config import get_settings
from backend.db.postgres import MistakeLog, PaperTrade, StockStrategyMap, get_config_value, session_scope
from backend.engine.kill_switch import KillSwitch
from backend.engine.paper_trader_v2 import PaperTrader
from backend.engine.regime_detector import (
    detect_regime,
    regime_is_high_volatility,
    regime_is_ranging,
    regime_is_trending,
    regime_trend_direction,
)
from backend.engine.scoring_engine import ScoringEngine
from backend.engine.stock_intelligence_engine import StockIntelligence, StockIntelligenceEngine
from backend.strategies.bb_squeeze import BollingerBandSqueezeStrategy
from backend.strategies.breakout_volume import BreakoutVolumeStrategy
from backend.strategies.ema_crossover import EMACrossoverStrategy
from backend.strategies.golden_cross import GoldenCrossStrategy
from backend.strategies.macd_momentum import MACDMomentumStrategy
from backend.strategies.news_driven import NewsDrivenMomentumStrategy
from backend.strategies.regime_aware_combined import RegimeAwareCombinedStrategy
from backend.strategies.rsi_mean_reversion import RSIMeanReversionStrategy
from backend.strategies.base_strategy import StrategyContext


settings = get_settings()


class SignalEngine:
    DEFAULT_INTRADAY_FALLBACK = "Combined Regime-Aware"
    TREND_FOLLOWING = {"EMA Crossover", "Golden Cross", "MACD Momentum", "Breakout with Volume"}
    MEAN_REVERSION = {"RSI Mean Reversion"}

    def __init__(
        self,
        scoring_engine: ScoringEngine | None = None,
        paper_trader: PaperTrader | None = None,
        kill_switch: KillSwitch | None = None,
        intelligence_engine: StockIntelligenceEngine | None = None,
    ) -> None:
        self.scoring_engine = scoring_engine or ScoringEngine()
        self.paper_trader = paper_trader or PaperTrader()
        self.kill_switch = kill_switch or KillSwitch()
        self.intelligence_engine = intelligence_engine or StockIntelligenceEngine()
        self.strategy_registry = {
            "EMA Crossover": EMACrossoverStrategy(),
            "Golden Cross": GoldenCrossStrategy(),
            "RSI Mean Reversion": RSIMeanReversionStrategy(),
            "MACD Momentum": MACDMomentumStrategy(),
            "Bollinger Band Squeeze": BollingerBandSqueezeStrategy(),
            "Breakout with Volume": BreakoutVolumeStrategy(),
            "News-Driven Momentum": NewsDrivenMomentumStrategy(),
            "Combined Regime-Aware": RegimeAwareCombinedStrategy(),
        }

    def _select_strategy_name(self, symbol: str, *, preferred_signal_type: str | None = None) -> str:
        with session_scope() as session:
            per_stock = session.get(StockStrategyMap, symbol)
            if per_stock and per_stock.best_strategy:
                candidate = per_stock.best_strategy
                if candidate in self.strategy_registry and (
                    preferred_signal_type is None or self.strategy_registry[candidate].signal_type == preferred_signal_type
                ):
                    return candidate
            global_best = get_config_value(session, "global_best_strategy", {"name": "Combined Regime-Aware"})
        candidate = global_best.get("name") or "Combined Regime-Aware"
        if candidate in self.strategy_registry and (
            preferred_signal_type is None or self.strategy_registry[candidate].signal_type == preferred_signal_type
        ):
            return candidate
        if preferred_signal_type == "INTRADAY":
            return self.DEFAULT_INTRADAY_FALLBACK
        return "Combined Regime-Aware"

    def _regime_match(self, strategy_name: str, regime: str) -> float:
        if regime_is_ranging(regime):
            return 1.0 if strategy_name in self.MEAN_REVERSION else 0.2
        if regime_is_high_volatility(regime):
            return 0.35 if strategy_name in self.TREND_FOLLOWING else 0.45
        if regime_is_trending(regime):
            if regime.endswith("MATURE"):
                return 1.0 if strategy_name in self.TREND_FOLLOWING or strategy_name == "Combined Regime-Aware" else 0.35
            return 0.85 if strategy_name in self.TREND_FOLLOWING or strategy_name == "Combined Regime-Aware" else 0.45
        return 0.55

    @staticmethod
    def _bounded(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
        return max(lower, min(upper, value))

    def _trend_alignment_score(self, latest: pd.Series, direction: str) -> float:
        close = float(latest.get("Close") or 0.0)
        ema_20 = float(latest.get("EMA_20") or close or 0.0)
        sma_50 = float(latest.get("SMA_50") or ema_20 or close or 0.0)
        sma_200 = float(latest.get("SMA_200") or sma_50 or close or 0.0)
        if direction == "SELL":
            checks = [
                1.0 if close < ema_20 else 0.0,
                1.0 if ema_20 < sma_50 else 0.0,
                1.0 if sma_50 < sma_200 else 0.0,
            ]
        else:
            checks = [
                1.0 if close > ema_20 else 0.0,
                1.0 if ema_20 > sma_50 else 0.0,
                1.0 if sma_50 > sma_200 else 0.0,
            ]
        return sum(checks) / len(checks)

    def _technical_setup_score(self, df: pd.DataFrame, signal: dict[str, Any]) -> float:
        latest = df.iloc[-1]
        direction = str(signal.get("signal") or "BUY").upper()
        strategy_name = str(signal.get("strategy_name") or "")
        ma_alignment = self._trend_alignment_score(latest, direction)
        close = float(latest.get("Close") or 0.0)
        adx = float(latest.get("ADX") or 0.0)
        rsi = float(latest.get("RSI_14") or 50.0)
        volume_base = float(latest.get("Volume_SMA_20") or latest.get("Volume") or 1.0)
        volume_ratio = float((latest.get("Volume") or 0.0) / max(volume_base, 1.0))

        if strategy_name in self.MEAN_REVERSION:
            if direction == "SELL":
                rsi_score = 1.0 if 50.0 <= rsi <= 72.0 else 0.65 if 45.0 <= rsi <= 78.0 else 0.3
                level_distance = abs(close - float(latest.get("High_63") or close)) / max(close, 0.01)
            else:
                rsi_score = 1.0 if 28.0 <= rsi <= 52.0 else 0.65 if 24.0 <= rsi <= 58.0 else 0.3
                level_distance = abs(close - float(latest.get("Low_63") or close)) / max(close, 0.01)
            adx_score = 1.0 if adx <= 24.0 else 0.55 if adx <= 30.0 else 0.25
            level_score = 1.0 if level_distance <= 0.02 else 0.7 if level_distance <= 0.04 else 0.35
        else:
            if direction == "SELL":
                rsi_score = 1.0 if 32.0 <= rsi <= 58.0 else 0.65 if 25.0 <= rsi <= 65.0 else 0.3
            else:
                rsi_score = 1.0 if 45.0 <= rsi <= 68.0 else 0.65 if 38.0 <= rsi <= 74.0 else 0.3
            adx_score = 1.0 if adx >= 22.0 else 0.65 if adx >= 16.0 else 0.35
            level_score = 0.75 if strategy_name == "Golden Cross" else 0.6

        candle_score = 1.0 if signal.get("pattern_name") else 0.55
        volume_score = self._bounded(volume_ratio / 2.5)
        return self._bounded(
            (ma_alignment * 0.28)
            + (rsi_score * 0.18)
            + (adx_score * 0.18)
            + (level_score * 0.16)
            + (candle_score * 0.10)
            + (volume_score * 0.10)
        )

    def _intraday_context_snapshot(
        self,
        *,
        daily_df: pd.DataFrame,
        intraday_df: pd.DataFrame,
        direction: str,
    ) -> dict[str, Any]:
        latest = intraday_df.iloc[-1]
        close = float(latest.get("Close") or 0.0)
        vwap_raw = latest.get("VWAP")
        vwap = float(vwap_raw) if vwap_raw is not None and pd.notna(vwap_raw) else close
        day_high = float(intraday_df["High"].max())
        day_low = float(intraday_df["Low"].min())
        day_range = max(day_high - day_low, 0.01)
        day_range_pct = (day_range / max(close, 0.01)) * 100.0
        range_position = (close - day_low) / day_range

        opening_window = intraday_df.head(min(6, len(intraday_df)))
        opening_high = float(opening_window["High"].max())
        opening_low = float(opening_window["Low"].min())

        recent_volume = float(intraday_df["Volume"].tail(min(6, len(intraday_df))).mean())
        baseline_window = intraday_df["Volume"].tail(min(24, len(intraday_df)))
        baseline_volume = float(baseline_window.mean()) if not baseline_window.empty else recent_volume
        recent_volume_ratio = recent_volume / max(baseline_volume, 1.0)

        confidence_delta = 0.0
        reasons: list[str] = []

        if direction == "SELL":
            if close < vwap:
                confidence_delta += 4.0
                reasons.append("Price is trading below VWAP, which supports a short intraday bias.")
            else:
                confidence_delta -= 6.0
                reasons.append("Price is above VWAP, so the short setup is less attractive.")

            if range_position <= 0.35:
                confidence_delta += 4.0
                reasons.append("Price is holding in the lower part of the intraday range.")
            elif range_position >= 0.60:
                confidence_delta -= 4.0
                reasons.append("Price is still in the upper half of the intraday range, which weakens the short setup.")

            if close <= opening_low * 1.002:
                confidence_delta += 5.0
                reasons.append("Price is near or below the opening-range low, which supports breakdown follow-through.")
            elif close > opening_high:
                confidence_delta -= 5.0
                reasons.append("Price reclaimed the opening range, which weakens the short setup.")
        else:
            if close > vwap:
                confidence_delta += 4.0
                reasons.append("Price is trading above VWAP, which supports a long intraday bias.")
            else:
                confidence_delta -= 6.0
                reasons.append("Price is below VWAP, so the long setup is less attractive.")

            if range_position >= 0.65:
                confidence_delta += 4.0
                reasons.append("Price is holding in the upper part of the intraday range.")
            elif range_position <= 0.40:
                confidence_delta -= 4.0
                reasons.append("Price is still in the lower half of the intraday range, which weakens the long setup.")

            if close >= opening_high * 0.998:
                confidence_delta += 5.0
                reasons.append("Price is near or above the opening-range high, which supports breakout follow-through.")
            elif close < opening_low:
                confidence_delta -= 5.0
                reasons.append("Price lost the opening range, which weakens the long setup.")

        if recent_volume_ratio >= 1.20:
            confidence_delta += 3.0
            reasons.append("Recent intraday volume is expanding versus the session average.")
        elif recent_volume_ratio <= 0.75:
            confidence_delta -= 2.0
            reasons.append("Recent intraday volume is fading, so follow-through risk is higher.")

        if day_range_pct >= 1.2:
            confidence_delta += 2.0
            reasons.append("The stock has enough intraday range expansion for a meaningful move.")
        elif day_range_pct <= 0.45:
            confidence_delta -= 3.0
            reasons.append("The stock has not expanded enough intraday range yet, so reward potential is weaker.")

        if not daily_df.empty:
            daily_latest = daily_df.iloc[-1]
            daily_close = float(daily_latest.get("Close") or close)
            daily_ema20 = float(daily_latest.get("EMA_20") or daily_close)
            daily_sma50 = float(daily_latest.get("SMA_50") or daily_close)
            if direction == "SELL":
                if daily_close < daily_ema20 < daily_sma50:
                    confidence_delta += 3.0
                    reasons.append("The daily trend also leans bearish, which supports the intraday short.")
                elif daily_close > daily_sma50:
                    confidence_delta -= 4.0
                    reasons.append("The daily trend is still supportive, so short conviction is trimmed.")
            else:
                if daily_close > daily_ema20 > daily_sma50:
                    confidence_delta += 3.0
                    reasons.append("The daily trend also leans bullish, which supports the intraday long.")
                elif daily_close < daily_sma50:
                    confidence_delta -= 4.0
                    reasons.append("The daily trend is still weak, so long conviction is trimmed.")

        return {
            "confidence_delta": confidence_delta,
            "reasons": reasons[:6],
            "snapshot": {
                "vwap": round(vwap, 2),
                "day_high": round(day_high, 2),
                "day_low": round(day_low, 2),
                "opening_range_high": round(opening_high, 2),
                "opening_range_low": round(opening_low, 2),
                "recent_volume_ratio": round(recent_volume_ratio, 2),
                "day_range_pct": round(day_range_pct, 2),
                "range_position": round(range_position, 3),
            },
        }

    def _build_signal_features(self, df: pd.DataFrame, signal: dict[str, Any], news_score: float, regime: str, fundamental_score: float) -> dict[str, float]:
        latest = df.iloc[-1]
        direction = str(signal.get("signal") or "BUY").upper()
        ma_alignment = self._trend_alignment_score(latest, direction)
        setup_quality = self._technical_setup_score(df, signal)
        if direction == "SELL":
            news_score_norm = min(1.0, max(0.0, (5.0 - news_score) / 10.0))
            directional_fundamental = 1.0 - float(fundamental_score)
        else:
            news_score_norm = min(1.0, max(0.0, (news_score + 5.0) / 10.0))
            directional_fundamental = float(fundamental_score)
        pattern_strength = self._bounded((setup_quality * 0.7) + ((1.0 if signal.get("pattern_name") else 0.45) * 0.3))
        volume_ratio = self._bounded(float((latest["Volume"] / max(latest["Volume_SMA_20"], 1)) / 2.5))
        return {
            "pattern_strength": pattern_strength,
            "ma_alignment": ma_alignment,
            "volume_ratio": volume_ratio,
            "news_score_norm": news_score_norm,
            "regime_match": self._regime_match(signal["strategy_name"], regime),
            "fundamental_score": min(1.0, max(0.0, directional_fundamental)),
        }

    @staticmethod
    def _apply_intelligence_confidence(confidence: float, intelligence: StockIntelligence, *, direction: str, signal_type: str) -> tuple[float | None, list[str]]:
        adjustment = intelligence.directional_adjustment(direction=direction, signal_type=signal_type)
        if adjustment.blocked:
            return None, adjustment.reasons
        adjusted = min(100.0, max(0.0, confidence + adjustment.confidence_delta))
        return adjusted, adjustment.reasons

    @staticmethod
    def _has_positive_results_catalyst(combined_news_score: float, event_flags: list[str] | None = None) -> bool:
        if combined_news_score >= settings.bearish_buy_news_override_score:
            return True
        normalized_flags = [str(flag).lower() for flag in (event_flags or [])]
        return any(
            marker in flag
            for flag in normalized_flags
            for marker in ("fresh results catalyst", "profit growth +", "earnings beat", "guidance upgrade")
        )

    @staticmethod
    def _apply_bearish_buy_penalty(
        confidence: float,
        *,
        direction: str,
        signal_type: str,
        regime: str,
        combined_news_score: float,
        daily_trend_bear: bool = False,
        event_flags: list[str] | None = None,
    ) -> tuple[float, list[str]]:
        if direction.upper() != "BUY" or signal_type.upper() == "INVESTMENT":
            return confidence, []
        bear_regime = regime_trend_direction(regime) == "BEAR"
        if not bear_regime and not daily_trend_bear:
            return confidence, []
        penalty = settings.bearish_buy_penalty_points + (3.0 if bear_regime and daily_trend_bear else 0.0)
        reasons: list[str] = []
        if bear_regime:
            reasons.append("Long setup is fighting a bearish regime, so confidence was trimmed instead of blocking the trade.")
        if daily_trend_bear:
            reasons.append("Daily trend is still below the long-term trend line, so long conviction was reduced.")
        if SignalEngine._has_positive_results_catalyst(combined_news_score, event_flags):
            penalty = max(4.0, penalty - settings.bearish_buy_news_penalty_relief)
            reasons.append("Strong positive news/results catalyst kept the long setup alive despite the weak tape.")
        adjusted = max(0.0, confidence - penalty)
        return adjusted, reasons[:3]

    def _maybe_use_news_catalyst_signal(
        self,
        *,
        signal: dict[str, Any],
        df: pd.DataFrame,
        regime: str,
        combined_news_score: float,
        signal_type: str,
        timeframe: str,
        event_flags: list[str] | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        if signal_type.upper() != "INTRADAY":
            return signal, []
        if not self._has_positive_results_catalyst(combined_news_score, event_flags):
            return signal, []
        current_direction = str(signal.get("signal") or "HOLD").upper()
        if current_direction == "BUY":
            return signal, []
        news_strategy = self.strategy_registry.get("News-Driven Momentum")
        if news_strategy is None:
            return signal, []
        news_signal = news_strategy.generate_signal(
            df,
            date=df.index[-1],
            context=StrategyContext(
                news_score=combined_news_score,
                regime=regime,
                signal_type="INTRADAY",
                timeframe=timeframe,
            ),
        )
        if str(news_signal.get("signal") or "HOLD").upper() != "BUY":
            return signal, []
        return news_signal, ["Fresh positive financial/news catalyst promoted a news-driven intraday BUY setup."]

    def _maybe_rescue_intraday_buy_signal(
        self,
        *,
        signal: dict[str, Any],
        intraday_df: pd.DataFrame,
        daily_df: pd.DataFrame,
        regime: str,
        combined_news_score: float,
        event_flags: list[str] | None,
    ) -> tuple[dict[str, Any], list[str]]:
        current_direction = str(signal.get("signal") or "HOLD").upper()
        if current_direction == "BUY":
            return signal, []
        latest = intraday_df.iloc[-1]
        close = float(latest.get("Close") or 0.0)
        ema_20 = float(latest.get("EMA_20") or close or 0.0)
        rsi = float(latest.get("RSI_14") or 50.0)
        volume_base = float(latest.get("Volume_SMA_20") or latest.get("Volume") or 1.0)
        volume_ratio = float((latest.get("Volume") or 0.0) / max(volume_base, 1.0))
        has_positive_catalyst = self._has_positive_results_catalyst(combined_news_score, event_flags)

        daily_supportive = False
        if not daily_df.empty:
            daily_latest = daily_df.iloc[-1]
            daily_close = float(daily_latest.get("Close") or close)
            daily_ema20 = float(daily_latest.get("EMA_20") or daily_close)
            daily_sma50 = float(daily_latest.get("SMA_50") or daily_close)
            daily_supportive = daily_close >= daily_ema20 or daily_close >= daily_sma50

        bullish_intraday_structure = (
            close >= ema_20 * 0.997
            and 40.0 <= rsi <= 74.0
            and volume_ratio >= 0.80
        )
        if not has_positive_catalyst and not (combined_news_score >= max(settings.news_momentum_sentiment_threshold, 0.35) and bullish_intraday_structure and daily_supportive):
            return signal, []

        rescue_strategies = ["News-Driven Momentum", "MACD Momentum", "EMA Crossover"]
        rescue_reasons: list[str] = []
        for strategy_name in rescue_strategies:
            strategy = self.strategy_registry.get(strategy_name)
            if strategy is None:
                continue
            candidate = strategy.generate_signal(
                intraday_df,
                date=intraday_df.index[-1],
                context=StrategyContext(
                    news_score=combined_news_score,
                    regime=regime,
                    signal_type="INTRADAY",
                    timeframe="INTRADAY",
                ),
            )
            if str(candidate.get("signal") or "HOLD").upper() != "BUY":
                continue
            rescue_reasons.append(
                "Positive news/results plus supportive intraday structure promoted a long-side intraday rescue candidate."
            )
            return candidate, rescue_reasons
        return signal, []

    def _apply_recent_mistake_penalty(
        self,
        confidence: float,
        *,
        symbol: str,
        strategy_name: str,
        pattern_name: str | None,
        regime: str,
        direction: str,
        opened_from: str,
    ) -> tuple[float, list[str]]:
        cutoff = datetime.now(tz=settings.tzinfo) - timedelta(days=30)
        penalty = 0.0
        reasons: list[str] = []
        with session_scope() as session:
            rows = session.execute(
                select(MistakeLog, PaperTrade)
                .join(PaperTrade, PaperTrade.trade_id == MistakeLog.trade_id)
                .where(MistakeLog.created_at >= cutoff)
                .order_by(MistakeLog.created_at.desc())
            ).all()

        for mistake, trade in rows:
            conditions = mistake.conditions_at_loss or {}
            matched = False
            if trade.stock_symbol == symbol and trade.strategy_name == strategy_name:
                penalty += 8.0
                matched = True
                reasons.append(f"Recent loss found on {symbol} with {strategy_name}; confidence trimmed.")
            elif (
                conditions.get("pattern") == pattern_name
                and conditions.get("regime") == regime
                and str(conditions.get("direction") or "").upper() == direction.upper()
            ):
                penalty += 7.0
                matched = True
                reasons.append(f"Similar {direction.lower()} setup recently lost in {regime}; confidence reduced.")
            elif str(conditions.get("sourceKind") or "") == str(opened_from or ""):
                penalty += 4.0
                matched = True
                reasons.append("Recent losses in the same setup source reduced confidence slightly.")
            if matched and penalty >= 20.0:
                break

        if penalty <= 0:
            return confidence, []
        return max(0.0, confidence - min(penalty, 20.0)), reasons[:3]

    @staticmethod
    def _pattern_hits(latest: pd.Series, positive: list[str], negative: list[str]) -> str:
        bullish = [name for name in positive if latest.get(name, 0) > 0]
        bearish = [name for name in negative if latest.get(name, 0) < 0]
        hits = bullish or bearish
        return ", ".join(hits) if hits else "none"

    def _build_strategy_reason(
        self,
        strategy_name: str,
        df: pd.DataFrame,
        signal: dict[str, Any],
        *,
        regime: str,
        news_score: float,
    ) -> tuple[str, list[str]]:
        latest, previous = df.iloc[-1], df.iloc[-2]
        volume_ratio = float(latest["Volume"] / max(latest.get("Volume_SMA_20", latest["Volume"]), 1))
        bullish_patterns = ["HAMMER", "ENGULFING", "MORNING_STAR", "THREE_WHITE"]
        bearish_patterns = ["EVENING_STAR", "SHOOTING_ST", "DARK_CLOUD", "THREE_BLACK"]

        direction = str(signal.get("signal") or "BUY").upper()

        if strategy_name == "EMA Crossover":
            if direction == "SELL":
                reason = (
                    f"9 EMA crossed below 20 EMA while the close stayed below 50 SMA "
                    f"({latest['EMA_9']:.2f} < {latest['EMA_20']:.2f}, close {latest['Close']:.2f}, 50 SMA {latest['SMA_50']:.2f})."
                )
                basis = [
                    f"EMA9 moved from {previous['EMA_9']:.2f} to {latest['EMA_9']:.2f} against EMA20 {previous['EMA_20']:.2f} to {latest['EMA_20']:.2f}.",
                    f"Trend filter passed because close {latest['Close']:.2f} is below SMA50 {latest['SMA_50']:.2f}.",
                    f"ATR-based stop uses ATR14 {latest['ATR_14']:.2f}.",
                ]
            else:
                reason = (
                    f"9 EMA crossed above 20 EMA while the close stayed above 50 SMA "
                    f"({latest['EMA_9']:.2f} > {latest['EMA_20']:.2f}, close {latest['Close']:.2f}, 50 SMA {latest['SMA_50']:.2f})."
                )
                basis = [
                    f"EMA9 moved from {previous['EMA_9']:.2f} to {latest['EMA_9']:.2f} against EMA20 {previous['EMA_20']:.2f} to {latest['EMA_20']:.2f}.",
                    f"Trend filter passed because close {latest['Close']:.2f} is above SMA50 {latest['SMA_50']:.2f}.",
                    f"ATR-based stop uses ATR14 {latest['ATR_14']:.2f}.",
                ]
        elif strategy_name == "Golden Cross":
            reason = (
                f"50 SMA crossed above 200 SMA on the daily chart "
                f"({latest['SMA_50']:.2f} vs {latest['SMA_200']:.2f}), triggering a long-term trend entry."
            )
            basis = [
                f"Previous session had SMA50 {previous['SMA_50']:.2f} against SMA200 {previous['SMA_200']:.2f}.",
                "This is treated as an investment setup with a trailing stop rather than an intraday trade.",
                f"Current close is {latest['Close']:.2f}.",
            ]
        elif strategy_name == "RSI Mean Reversion":
            if direction == "SELL":
                reason = (
                    f"RSI rolled lower from an overbought zone, moving from {previous['RSI_14']:.2f} to {latest['RSI_14']:.2f}, "
                    f"which signals a mean-reversion short setup."
                )
                basis = [
                    f"Recent swing high used for stop placement is {df['High'].tail(5).max():.2f}.",
                    f"Close is {latest['Close']:.2f} and ATR14 is {latest['ATR_14']:.2f}.",
                    f"Regime at evaluation was {regime}.",
                ]
            else:
                reason = (
                    f"RSI reversed up through the oversold zone, moving from {previous['RSI_14']:.2f} to {latest['RSI_14']:.2f}, "
                    f"which signals a mean-reversion bounce."
                )
                basis = [
                    f"Recent swing low used for stop placement is {df['Low'].tail(5).min():.2f}.",
                    f"Close is {latest['Close']:.2f} and ATR14 is {latest['ATR_14']:.2f}.",
                    f"Regime at evaluation was {regime}.",
                ]
        elif strategy_name == "MACD Momentum":
            if direction == "SELL":
                reason = (
                    f"MACD crossed below its signal line with price staying below SMA50 "
                    f"({latest['MACD']:.2f} vs {latest['MACD_Signal']:.2f}, hist {latest['MACD_Hist']:.2f})."
                )
                basis = [
                    f"Previous MACD relation was {previous['MACD']:.2f} vs {previous['MACD_Signal']:.2f}.",
                    f"Close {latest['Close']:.2f} is below SMA50 {latest['SMA_50']:.2f}.",
                    f"ATR14 stop buffer is {latest['ATR_14']:.2f}.",
                ]
            else:
                reason = (
                    f"MACD crossed above its signal line with positive histogram "
                    f"({latest['MACD']:.2f} vs {latest['MACD_Signal']:.2f}, hist {latest['MACD_Hist']:.2f}) "
                    f"while price stayed above SMA50."
                )
                basis = [
                    f"Previous MACD relation was {previous['MACD']:.2f} vs {previous['MACD_Signal']:.2f}.",
                    f"Close {latest['Close']:.2f} is above SMA50 {latest['SMA_50']:.2f}.",
                    f"ATR14 stop buffer is {latest['ATR_14']:.2f}.",
                ]
        elif strategy_name == "Bollinger Band Squeeze":
            if direction == "SELL":
                reason = (
                    f"Bollinger Band width compressed below its 20-day average and price broke below the lower band "
                    f"with expansion risk ({latest['BB_Width']:.2f} vs avg {latest['BB_Width_Avg_20']:.2f})."
                )
                basis = [
                    f"Close {latest['Close']:.2f} is near the lower band {latest['BB_Lower']:.2f}.",
                    f"Volume is running at {volume_ratio:.2f}x the 20-day average.",
                    f"Upper band resistance for stop is {latest['BB_Upper']:.2f}.",
                ]
            else:
                reason = (
                    f"Bollinger Band width compressed below its 20-day average and price broke above the upper band "
                    f"with strong volume ({latest['BB_Width']:.2f} vs avg {latest['BB_Width_Avg_20']:.2f})."
                )
                basis = [
                    f"Close {latest['Close']:.2f} is above the upper band {latest['BB_Upper']:.2f}.",
                    f"Volume is running at {volume_ratio:.2f}x the 20-day average.",
                    f"Lower band support for stop is {latest['BB_Lower']:.2f}.",
                ]
        elif strategy_name == "Breakout with Volume":
            low_20 = latest.get("Low_20", latest["Low"])
            if direction == "SELL":
                reason = (
                    f"Price slipped below the 20-day support zone with downside pressure "
                    f"({latest['Close']:.2f} against low trigger {low_20:.2f}, volume {volume_ratio:.2f}x average)."
                )
                basis = [
                    f"20-day breakdown level is {low_20:.2f}.",
                    f"ATR14 is {latest['ATR_14']:.2f}, which sets the stop buffer.",
                    f"Pattern tagged as {signal.get('pattern_name')}.",
                ]
            else:
                reason = (
                    f"Price closed above the 20-day high with breakout volume "
                    f"({latest['Close']:.2f} above {latest['High_20']:.2f}, volume {volume_ratio:.2f}x average)."
                )
                basis = [
                    f"20-day breakout level is {latest['High_20']:.2f}.",
                    f"ATR14 is {latest['ATR_14']:.2f}, which sets the stop buffer.",
                    f"Pattern tagged as {signal.get('pattern_name')}.",
                ]
        elif strategy_name == "Support and Resistance":
            pattern_hits = self._pattern_hits(latest, bullish_patterns, bearish_patterns)
            if direction == "SELL":
                reason = (
                    f"Price is trading near the 3-month resistance zone and a bearish rejection pattern appeared "
                    f"({pattern_hits})."
                )
                basis = [
                    f"Resistance is {latest['High_63']:.2f} and support is {latest['Low_63']:.2f}.",
                    f"Close {latest['Close']:.2f} is within roughly 2% of the key level.",
                    f"Detected candle confirmation: {pattern_hits}.",
                ]
            else:
                reason = (
                    f"Price is trading near the 3-month support zone and a confirming candlestick pattern appeared "
                    f"({pattern_hits})."
                )
                basis = [
                    f"Support is {latest['Low_63']:.2f} and resistance is {latest['High_63']:.2f}.",
                    f"Close {latest['Close']:.2f} is within roughly 2% of the key level.",
                    f"Detected candle confirmation: {pattern_hits}.",
                ]
        elif strategy_name == "News-Driven Momentum":
            if direction == "SELL":
                reason = (
                    f"News sentiment was weak at {news_score:.2f}, momentum cooled to RSI {latest['RSI_14']:.2f}, "
                    f"and price remained below EMA20 {latest['EMA_20']:.2f}."
                )
                basis = [
                    f"Close is {latest['Close']:.2f}.",
                    f"News filter used the latest stored sentiment score of {news_score:.2f}.",
                    f"Volume ratio is {volume_ratio:.2f}x average.",
                ]
            else:
                reason = (
                    f"News sentiment was strong at {news_score:.2f}, RSI stayed in the healthy momentum zone at {latest['RSI_14']:.2f}, "
                    f"and price remained above EMA20 {latest['EMA_20']:.2f}."
                )
                basis = [
                    f"Close is {latest['Close']:.2f}.",
                    f"News filter used the latest stored sentiment score of {news_score:.2f}.",
                    f"Volume ratio is {volume_ratio:.2f}x average.",
                ]
        else:
            active_regime = signal.get("meta", {}).get("active_regime", regime)
            if direction == "SELL":
                reason = f"Regime-aware logic selected the {active_regime} rule set and the resulting setup met the short-side daily signal criteria."
            else:
                reason = f"Regime-aware logic selected the {active_regime} rule set and the resulting setup met the daily signal criteria."
            basis = [
                f"Detected regime: {active_regime}.",
                f"Close {latest['Close']:.2f}, RSI14 {latest['RSI_14']:.2f}, ADX {latest['ADX']:.2f}.",
                f"Volume ratio is {volume_ratio:.2f}x average and news score is {news_score:.2f}.",
            ]
        return reason, basis

    def _attach_explanation(
        self,
        symbol: str,
        df: pd.DataFrame,
        signal: dict[str, Any],
        *,
        regime: str,
        news_score: float,
        features: dict[str, float],
        intelligence: StockIntelligence | None = None,
        adjustment_reasons: list[str] | None = None,
    ) -> None:
        latest = df.iloc[-1]
        reason, basis = self._build_strategy_reason(signal["strategy_name"], df, signal, regime=regime, news_score=news_score)
        regime_line = f"Regime filter: {regime}."
        scoring_line = (
            "Scoring inputs "
            f"pattern {features['pattern_strength']:.2f}, MA {features['ma_alignment']:.2f}, "
            f"volume {features['volume_ratio']:.2f}, news {features['news_score_norm']:.2f}, "
            f"regime {features['regime_match']:.2f}, fundamentals {features['fundamental_score']:.2f}."
        )
        price_context_line = f"Close {latest['Close']:.2f}, RSI14 {latest['RSI_14']:.2f}, ADX {latest['ADX']:.2f}, ATR14 {latest['ATR_14']:.2f}."
        basis.extend(
            [
                regime_line,
                f"News score used: {news_score:.2f}.",
                scoring_line,
                price_context_line,
            ]
        )
        technical_section = [reason, *basis[:3], price_context_line]
        news_section = [f"Event-adjusted news score is {news_score:.2f}."]
        sector_section: list[str] = []
        fundamental_section: list[str] = []
        risk_section = [regime_line, scoring_line]
        if intelligence is not None:
            sector_line = (
                f"Sector context: {intelligence.sector_strength.sector} is "
                f"{intelligence.sector_strength.label.lower()} with score {intelligence.sector_strength.score:.2f}."
            )
            fundamental_line = (
                f"Fundamental quality score is {intelligence.scoring_fundamental_score:.2f} "
                f"with confidence {intelligence.fundamental.confidence:.2f}."
            )
            valuation_line = (
                f"Valuation looks {intelligence.valuation_label.lower()} with score {intelligence.valuation_score:.2f}; "
                f"selection score is {intelligence.selection_score:.2f} ({intelligence.selection_label.lower().replace('_', ' ')})."
            )
            outlook_line = f"Business outlook score is {intelligence.business_outlook_score:.2f}. {intelligence.fundamental.selection_summary}"
            data_source_line = (
                "Structured balance-sheet data is available for this stock."
                if intelligence.fundamental.has_snapshot
                else "Structured balance-sheet data is not stored, so financial-news cues are carrying more weight."
            )
            event_line = f"Event-adjusted news score is {intelligence.combined_news_score:.2f}."
            basis.extend([sector_line, fundamental_line, valuation_line, outlook_line, event_line, data_source_line])
            sector_section.extend([sector_line, *intelligence.sector_strength.notes[:2]])
            fundamental_section.extend([fundamental_line, valuation_line, outlook_line, data_source_line, *intelligence.fundamental.flags[:2]])
            news_section.extend([event_line, *intelligence.event.notes[:2], *intelligence.event.event_flags[:2]])
            if intelligence.fundamental.days_to_earnings is not None:
                risk_section.append(f"Days to earnings: {intelligence.fundamental.days_to_earnings}.")
        if adjustment_reasons:
            basis.extend(adjustment_reasons[:3])
            risk_section.extend(adjustment_reasons[:3])
        signal["recommendation_reason"] = reason
        signal["basis_points"] = basis[:10]
        signal["explanation_sections"] = {
            "technical": technical_section[:5],
            "news": news_section[:5],
            "sector": sector_section[:4],
            "fundamentals": fundamental_section[:5],
            "risk": risk_section[:5],
        }
        signal["analysis_snapshot"] = {
            "symbol": symbol,
            "close": float(latest["Close"]),
            "rsi_14": float(latest["RSI_14"]),
            "adx": float(latest["ADX"]),
            "atr_14": float(latest["ATR_14"]),
            "volume_ratio": float(latest["Volume"] / max(latest.get("Volume_SMA_20", latest["Volume"]), 1)),
        }
        if intelligence is not None:
            signal["analysis_snapshot"].update(
                {
                    "sector": intelligence.sector,
                    "sector_score": intelligence.sector_strength.score,
                    "fundamental_score": intelligence.scoring_fundamental_score,
                    "days_to_earnings": intelligence.fundamental.days_to_earnings,
                    "event_score": intelligence.event.event_score,
                    "event_positive_results_catalyst": intelligence.event.positive_results_catalyst,
                    "event_financial_catalyst_score": intelligence.event.financial_catalyst_score,
                    "fundamental_has_snapshot": intelligence.fundamental.has_snapshot,
                    "fundamental_confidence": intelligence.fundamental.confidence,
                    "valuation_score": intelligence.valuation_score,
                    "valuation_label": intelligence.valuation_label,
                    "business_outlook_score": intelligence.business_outlook_score,
                    "selection_score": intelligence.selection_score,
                    "selection_label": intelligence.selection_label,
                    "financial_data_source": "STRUCTURED_SNAPSHOT" if intelligence.fundamental.has_snapshot else "NEWS_FALLBACK",
                }
            )

    def evaluate_symbol(
        self,
        symbol: str,
        df: pd.DataFrame,
        *,
        news_score: float = 0.0,
        fundamental_score: float = 0.5,
        portfolio_value: float | None = None,
        signal_type_override: str | None = None,
        strategy_name_override: str | None = None,
        open_trade: bool = True,
        long_only: bool = False,
        opened_from: str = "signal_engine",
        company_name: str | None = None,
    ) -> dict[str, Any] | None:
        safe, reason = self.kill_switch.check_all()
        if not safe:
            self.kill_switch.activate(reason)
            return None

        regime = detect_regime(df)
        effective_signal_type = signal_type_override if signal_type_override in {"INTRADAY", "INVESTMENT"} else None
        intelligence = self.intelligence_engine.build(
            symbol=symbol,
            company_name=company_name,
            as_of=datetime.now(tz=settings.tzinfo),
            signal_type=effective_signal_type or "INVESTMENT",
            base_news_score=news_score,
        )
        strategy_name = (
            strategy_name_override
            if strategy_name_override in self.strategy_registry
            else self._select_strategy_name(symbol, preferred_signal_type=effective_signal_type)
        )
        if (
            strategy_name_override is None
            and regime_is_ranging(regime)
            and strategy_name in {"EMA Crossover", "Golden Cross", "MACD Momentum", "Breakout with Volume"}
        ):
            strategy_name = "RSI Mean Reversion"
        strategy = self.strategy_registry[strategy_name]
        signal = strategy.generate_signal(
            df,
            date=df.index[-1],
            context=StrategyContext(
                news_score=intelligence.combined_news_score,
                regime=regime,
                signal_type=strategy.signal_type,
                timeframe="DAILY",
            ),
        )
        if signal["signal"] == "HOLD":
            return None
        if long_only and signal["signal"] != "BUY":
            return None

        scoring_fundamental_score = intelligence.scoring_fundamental_score if intelligence else fundamental_score
        combined_news_score = intelligence.combined_news_score if intelligence else news_score
        features = self._build_signal_features(df, signal, combined_news_score, regime, scoring_fundamental_score)
        confidence = self.scoring_engine.score(features)
        signal["signal_type"] = signal_type_override or signal["signal_type"]
        adjusted_confidence, adjustment_reasons = self._apply_intelligence_confidence(
            confidence,
            intelligence,
            direction=str(signal["signal"]),
            signal_type=str(signal["signal_type"]),
        )
        if adjusted_confidence is None:
            return None
        adjusted_confidence, bear_penalty_reasons = self._apply_bearish_buy_penalty(
            adjusted_confidence,
            direction=str(signal["signal"]),
            signal_type=str(signal["signal_type"]),
            regime=regime,
            combined_news_score=combined_news_score,
            event_flags=intelligence.event.event_flags,
        )
        adjusted_confidence, mistake_reasons = self._apply_recent_mistake_penalty(
            adjusted_confidence,
            symbol=symbol,
            strategy_name=signal["strategy_name"],
            pattern_name=signal.get("pattern_name"),
            regime=regime,
            direction=str(signal["signal"]),
            opened_from=opened_from,
        )
        adjustment_reasons = list(adjustment_reasons or []) + bear_penalty_reasons + mistake_reasons
        signal.update(
            {
                "stock_symbol": symbol,
                "confidence_score": adjusted_confidence,
                "news_score_at_entry": combined_news_score,
                "regime_at_entry": regime,
                "entry_zone_low": round(signal["entry_price"] * (1.0 - settings.watchlist_entry_zone_buffer_pct), 2),
                "entry_zone_high": round(signal["entry_price"] * (1.0 + settings.watchlist_entry_zone_buffer_pct), 2),
                "portfolio_value": portfolio_value or settings.paper_portfolio_value,
                "signal_timestamp": datetime.now(tz=settings.tzinfo).isoformat(),
                "feature_breakdown": features,
                "opened_from": opened_from,
                "sector": intelligence.sector,
                "sector_score": intelligence.sector_strength.score,
                "days_to_earnings": intelligence.fundamental.days_to_earnings,
                "event_score": intelligence.event.event_score,
                "event_flags": intelligence.event.event_flags,
                "event_positive_results_catalyst": intelligence.event.positive_results_catalyst,
                "event_financial_catalyst_score": intelligence.event.financial_catalyst_score,
                "event_catalyst_summary": intelligence.event.catalyst_summary,
                "fundamental_quality_score": intelligence.scoring_fundamental_score,
                "fundamental_has_snapshot": intelligence.fundamental.has_snapshot,
                "fundamental_confidence": intelligence.fundamental.confidence,
                "fundamental_raw_metrics": intelligence.fundamental.raw_metrics,
                "fundamental_growth_score": intelligence.fundamental.growth_score,
                "fundamental_balance_score": intelligence.fundamental.balance_sheet_score,
                "fundamental_business_quality_score": intelligence.fundamental.business_quality_score,
                "fundamental_ownership_score": intelligence.fundamental.ownership_score,
                "valuation_score": intelligence.valuation_score,
                "valuation_label": intelligence.valuation_label,
                "business_outlook_score": intelligence.business_outlook_score,
                "selection_score": intelligence.selection_score,
                "selection_label": intelligence.selection_label,
                "sector_peer_count": intelligence.fundamental.sector_peer_count,
                "sector_medians": intelligence.fundamental.sector_medians,
                "financial_data_source": "STRUCTURED_SNAPSHOT" if intelligence.fundamental.has_snapshot else "NEWS_FALLBACK",
                "intelligence_notes": intelligence.notes,
            }
        )
        self._attach_explanation(
            symbol,
            df,
            signal,
            regime=regime,
            news_score=combined_news_score,
            features=features,
            intelligence=intelligence,
            adjustment_reasons=adjustment_reasons,
        )
        if adjusted_confidence >= 70 and open_trade:
            try:
                trade_id = self.paper_trader.open_trade(signal)
            except RuntimeError as exc:
                signal["paper_trade_status"] = str(exc)
            else:
                signal["paper_trade_id"] = trade_id
                signal["paper_trade_status"] = "OPEN"
        elif adjusted_confidence >= 70:
            signal["paper_trade_status"] = "READY"
        elif adjusted_confidence < 55:
            return None
        else:
            signal["paper_trade_status"] = "NOT_OPENED"
        return signal

    def evaluate_intraday_symbol(
        self,
        symbol: str,
        daily_df: pd.DataFrame,
        intraday_df: pd.DataFrame,
        *,
        news_score: float = 0.0,
        fundamental_score: float = 0.5,
        portfolio_value: float | None = None,
        open_trade: bool = True,
        opened_from: str = "intraday_scan",
        company_name: str | None = None,
    ) -> dict[str, Any] | None:
        if intraday_df.empty or len(intraday_df) < 50:
            return None

        safe, reason = self.kill_switch.check_all()
        if not safe:
            self.kill_switch.activate(reason)
            return None

        regime = detect_regime(intraday_df)
        intelligence = self.intelligence_engine.build(
            symbol=symbol,
            company_name=company_name,
            as_of=datetime.now(tz=settings.tzinfo),
            signal_type="INTRADAY",
            base_news_score=news_score,
        )
        strategy_name = self._select_strategy_name(symbol, preferred_signal_type="INTRADAY")
        if regime_is_ranging(regime) and strategy_name in {"EMA Crossover", "MACD Momentum", "Breakout with Volume"}:
            strategy_name = "RSI Mean Reversion"

        strategy = self.strategy_registry[strategy_name]
        signal = strategy.generate_signal(
            intraday_df,
            date=intraday_df.index[-1],
            context=StrategyContext(
                news_score=intelligence.combined_news_score,
                regime=regime,
                signal_type="INTRADAY",
                timeframe="INTRADAY",
            ),
        )
        signal, news_override_reasons = self._maybe_use_news_catalyst_signal(
            signal=signal,
            df=intraday_df,
            regime=regime,
            combined_news_score=intelligence.combined_news_score,
            signal_type="INTRADAY",
            timeframe="INTRADAY",
            event_flags=intelligence.event.event_flags,
        )
        signal, rescue_buy_reasons = self._maybe_rescue_intraday_buy_signal(
            signal=signal,
            intraday_df=intraday_df,
            daily_df=daily_df,
            regime=regime,
            combined_news_score=intelligence.combined_news_score,
            event_flags=intelligence.event.event_flags,
        )
        if signal["signal"] == "HOLD":
            return None

        daily_trend_bear = False
        if not daily_df.empty:
            daily_latest = daily_df.iloc[-1]
            daily_sma_200 = daily_latest.get("SMA_200")
            if daily_sma_200 is not None and pd.notna(daily_sma_200):
                daily_trend_bear = float(daily_latest["Close"]) < float(daily_sma_200)

        scoring_fundamental_score = intelligence.scoring_fundamental_score if intelligence else fundamental_score
        combined_news_score = intelligence.combined_news_score if intelligence else news_score
        features = self._build_signal_features(intraday_df, signal, combined_news_score, regime, scoring_fundamental_score)
        if not daily_df.empty:
            daily_latest = daily_df.iloc[-1]
            if pd.notna(daily_latest.get("EMA_20")) and pd.notna(daily_latest.get("SMA_50")):
                features["ma_alignment"] = max(
                    features["ma_alignment"],
                    1.0 if daily_latest["Close"] > daily_latest["EMA_20"] > daily_latest["SMA_50"] else 0.4,
                )
        intraday_context = self._intraday_context_snapshot(
            daily_df=daily_df,
            intraday_df=intraday_df,
            direction=str(signal["signal"]).upper(),
        )
        confidence = self.scoring_engine.score(features)
        confidence = min(100.0, max(0.0, confidence + float(intraday_context["confidence_delta"])))
        adjusted_confidence, adjustment_reasons = self._apply_intelligence_confidence(
            confidence,
            intelligence,
            direction=str(signal["signal"]),
            signal_type="INTRADAY",
        )
        if adjusted_confidence is None:
            return None
        adjusted_confidence, bear_penalty_reasons = self._apply_bearish_buy_penalty(
            adjusted_confidence,
            direction=str(signal["signal"]),
            signal_type="INTRADAY",
            regime=regime,
            combined_news_score=combined_news_score,
            daily_trend_bear=daily_trend_bear,
            event_flags=intelligence.event.event_flags,
        )
        adjusted_confidence, mistake_reasons = self._apply_recent_mistake_penalty(
            adjusted_confidence,
            symbol=symbol,
            strategy_name=signal["strategy_name"],
            pattern_name=signal.get("pattern_name"),
            regime=regime,
            direction=str(signal["signal"]),
            opened_from=opened_from,
        )
        adjustment_reasons = (
            list(intraday_context["reasons"])
            + news_override_reasons
            + rescue_buy_reasons
            + list(adjustment_reasons or [])
            + bear_penalty_reasons
            + mistake_reasons
        )
        signal["signal_type"] = "INTRADAY"
        signal.update(
            {
                "stock_symbol": symbol,
                "confidence_score": adjusted_confidence,
                "news_score_at_entry": combined_news_score,
                "regime_at_entry": regime,
                "entry_zone_low": round(signal["entry_price"] * (1.0 - settings.watchlist_entry_zone_buffer_pct), 2),
                "entry_zone_high": round(signal["entry_price"] * (1.0 + settings.watchlist_entry_zone_buffer_pct), 2),
                "portfolio_value": portfolio_value or settings.paper_portfolio_value,
                "signal_timestamp": datetime.now(tz=settings.tzinfo).isoformat(),
                "feature_breakdown": features,
                "opened_from": opened_from,
                "sector": intelligence.sector,
                "sector_score": intelligence.sector_strength.score,
                "days_to_earnings": intelligence.fundamental.days_to_earnings,
                "event_score": intelligence.event.event_score,
                "event_flags": intelligence.event.event_flags,
                "event_positive_results_catalyst": intelligence.event.positive_results_catalyst,
                "event_financial_catalyst_score": intelligence.event.financial_catalyst_score,
                "event_catalyst_summary": intelligence.event.catalyst_summary,
                "fundamental_quality_score": intelligence.scoring_fundamental_score,
                "fundamental_has_snapshot": intelligence.fundamental.has_snapshot,
                "fundamental_confidence": intelligence.fundamental.confidence,
                "fundamental_raw_metrics": intelligence.fundamental.raw_metrics,
                "fundamental_growth_score": intelligence.fundamental.growth_score,
                "fundamental_balance_score": intelligence.fundamental.balance_sheet_score,
                "fundamental_business_quality_score": intelligence.fundamental.business_quality_score,
                "fundamental_ownership_score": intelligence.fundamental.ownership_score,
                "valuation_score": intelligence.valuation_score,
                "valuation_label": intelligence.valuation_label,
                "business_outlook_score": intelligence.business_outlook_score,
                "selection_score": intelligence.selection_score,
                "selection_label": intelligence.selection_label,
                "sector_peer_count": intelligence.fundamental.sector_peer_count,
                "sector_medians": intelligence.fundamental.sector_medians,
                "intraday_context": intraday_context["snapshot"],
                "financial_data_source": "STRUCTURED_SNAPSHOT" if intelligence.fundamental.has_snapshot else "NEWS_FALLBACK",
                "intelligence_notes": intelligence.notes,
            }
        )
        self._attach_explanation(
            symbol,
            intraday_df,
            signal,
            regime=regime,
            news_score=combined_news_score,
            features=features,
            intelligence=intelligence,
            adjustment_reasons=adjustment_reasons,
        )
        signal["analysis_snapshot"].update(intraday_context["snapshot"])
        open_threshold = 70.0
        ready_threshold = 70.0
        if (
            str(signal.get("signal") or "").upper() == "BUY"
            and bool(signal.get("event_positive_results_catalyst"))
        ):
            open_threshold = 64.0
            ready_threshold = 64.0

        if adjusted_confidence >= open_threshold and open_trade:
            try:
                trade_id = self.paper_trader.open_trade(signal)
            except RuntimeError as exc:
                signal["paper_trade_status"] = str(exc)
            else:
                signal["paper_trade_id"] = trade_id
                signal["paper_trade_status"] = "OPEN"
        elif adjusted_confidence >= ready_threshold:
            signal["paper_trade_status"] = "READY"
        elif adjusted_confidence < 55:
            return None
        else:
            signal["paper_trade_status"] = "NOT_OPENED"
        return signal
