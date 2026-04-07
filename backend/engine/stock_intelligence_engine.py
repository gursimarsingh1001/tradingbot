from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.engine.event_risk_engine import EventInsight, EventRiskEngine
from backend.engine.fundamental_engine import FundamentalEngine, FundamentalInsight
from backend.engine.sector_strength_engine import SectorInsight, SectorStrengthEngine


@dataclass(slots=True)
class DirectionalAdjustment:
    blocked: bool
    confidence_delta: float
    reasons: list[str]


@dataclass(slots=True)
class StockIntelligence:
    symbol: str
    sector: str
    fundamental: FundamentalInsight
    event: EventInsight
    sector_strength: SectorInsight
    scoring_fundamental_score: float
    selection_score: float
    business_outlook_score: float
    valuation_score: float
    valuation_label: str
    selection_label: str
    combined_news_score: float
    notes: list[str]

    def directional_adjustment(self, *, direction: str, signal_type: str) -> DirectionalAdjustment:
        direction = direction.upper()
        signal_type = signal_type.upper()
        blocked = False
        confidence_delta = 0.0
        reasons: list[str] = []

        if direction == "BUY":
            if self.fundamental.earnings_risk == "IMMINENT" and signal_type == "INVESTMENT":
                blocked = True
                reasons.append("Investment entry blocked because earnings are due today or by the next session.")
            if self.event.high_impact_negative:
                if signal_type == "INVESTMENT":
                    blocked = True
                    reasons.append("Investment entry blocked because recent news contains a high-impact negative event.")
                else:
                    confidence_delta -= 18.0
                    reasons.append("Intraday long confidence cut because recent news contains a high-impact negative event.")
            if self.event.event_score <= -0.4:
                confidence_delta -= 10.0
                reasons.append("Recent event flow is negative for new long entries.")
            if self.event.positive_results_catalyst:
                catalyst_bonus = 12.0 if signal_type == "INTRADAY" else 6.0
                catalyst_bonus += min(4.0, self.event.financial_catalyst_score * 4.0)
                confidence_delta += catalyst_bonus
                reasons.append(
                    self.event.catalyst_summary
                    or "Fresh financial results are strongly positive and support a long-side catalyst setup."
                )
            if self.scoring_fundamental_score >= 0.72:
                confidence_delta += 8.0 if signal_type == "INVESTMENT" else 4.0
                reasons.append("Strong financial quality supports the long setup.")
            elif self.scoring_fundamental_score <= 0.38:
                confidence_delta -= 12.0 if signal_type == "INVESTMENT" else 6.0
                reasons.append("Weak financial quality reduced confidence for the long setup.")
            if self.valuation_label == "CHEAP":
                confidence_delta += 6.0 if signal_type == "INVESTMENT" else 2.0
                reasons.append("Valuation looks attractive relative to the sector.")
            elif self.valuation_label == "EXPENSIVE":
                confidence_delta -= 8.0 if signal_type == "INVESTMENT" else 3.0
                reasons.append("Valuation looks stretched relative to the sector.")
            if self.business_outlook_score >= 0.68:
                confidence_delta += 6.0 if signal_type == "INVESTMENT" else 3.0
                reasons.append("Growth, margins, and balance-sheet trends point to a healthy forward outlook.")
            elif self.business_outlook_score <= 0.40:
                confidence_delta -= 7.0 if signal_type == "INVESTMENT" else 3.0
                reasons.append("Forward business outlook is weak, so long-side conviction was trimmed.")
            if self.sector_strength.score >= 0.68:
                confidence_delta += 6.0
                reasons.append("Sector breadth is strong, which supports follow-through.")
            elif self.sector_strength.score <= 0.35:
                confidence_delta -= 8.0
                reasons.append("Sector backdrop is weak, so follow-through risk is higher.")
            if signal_type == "INVESTMENT":
                if self.selection_score >= 0.72:
                    confidence_delta += 5.0
                    reasons.append("Overall stock-selection score is strong for a positional entry.")
                elif self.selection_score <= 0.42:
                    confidence_delta -= 7.0
                    reasons.append("Overall stock-selection score is weak for a positional entry.")
        else:
            if self.event.high_impact_negative or self.event.event_score <= -0.45:
                confidence_delta += 10.0
                reasons.append("Negative event flow supports the sell side.")
            if self.event.high_impact_positive or self.event.event_score >= 0.45:
                confidence_delta -= 8.0
                reasons.append("Positive event flow makes the sell side less attractive.")
            if self.event.positive_results_catalyst:
                confidence_delta -= 10.0 if signal_type == "INTRADAY" else 5.0
                reasons.append("Fresh positive financial results make the sell side less attractive.")
            if self.scoring_fundamental_score >= 0.75:
                confidence_delta -= 6.0
                reasons.append("Strong financial quality reduces conviction in a short-biased call.")
            elif self.scoring_fundamental_score <= 0.35:
                confidence_delta += 4.0
                reasons.append("Weak financial quality supports a bearish bias.")
            if self.valuation_label == "EXPENSIVE":
                confidence_delta += 4.0
                reasons.append("Rich valuation supports a bearish or profit-booking setup.")
            elif self.valuation_label == "CHEAP":
                confidence_delta -= 5.0
                reasons.append("Attractive valuation makes the sell side less attractive.")
            if self.business_outlook_score >= 0.68:
                confidence_delta -= 5.0
                reasons.append("Healthy forward outlook reduces short conviction.")
            elif self.business_outlook_score <= 0.40:
                confidence_delta += 4.0
                reasons.append("Weak business outlook supports a bearish bias.")

        return DirectionalAdjustment(blocked=blocked, confidence_delta=confidence_delta, reasons=reasons)


class StockIntelligenceEngine:
    def __init__(
        self,
        *,
        fundamental_engine: FundamentalEngine | None = None,
        event_risk_engine: EventRiskEngine | None = None,
        sector_strength_engine: SectorStrengthEngine | None = None,
    ) -> None:
        self.fundamental_engine = fundamental_engine or FundamentalEngine()
        self.event_risk_engine = event_risk_engine or EventRiskEngine()
        self.sector_strength_engine = sector_strength_engine or SectorStrengthEngine()

    def build(
        self,
        *,
        symbol: str,
        company_name: str | None,
        as_of: datetime,
        signal_type: str,
        base_news_score: float,
    ) -> StockIntelligence:
        fundamental = self.fundamental_engine.build_insight(symbol, company_name, as_of)
        sector_strength = self.sector_strength_engine.build_insight(symbol, company_name, fundamental.sector)
        event = self.event_risk_engine.build_insight(
            symbol,
            as_of=as_of,
            signal_type=signal_type,
            base_sentiment=base_news_score,
            days_to_earnings=fundamental.days_to_earnings,
        )
        news_normalized = max(0.0, min(1.0, (event.combined_news_score + 5.0) / 10.0))
        scoring_fundamental_score = max(
            0.0,
            min(
                1.0,
                (fundamental.score * 0.48)
                + (fundamental.business_quality_score * 0.12)
                + (fundamental.outlook_score * 0.12)
                + (fundamental.valuation_score * 0.08)
                + (sector_strength.score * 0.20)
                + (0.06 if not event.high_impact_negative else -0.08),
            ),
        )
        selection_score = max(
            0.0,
            min(
                1.0,
                (fundamental.score * 0.38)
                + (fundamental.business_quality_score * 0.14)
                + (fundamental.outlook_score * 0.14)
                + (fundamental.valuation_score * 0.12)
                + (sector_strength.score * 0.12)
                + (news_normalized * 0.10)
                + (0.04 if not event.high_impact_negative else -0.08),
            ),
        )
        if selection_score >= 0.72:
            selection_label = "HIGH_CONVICTION"
        elif selection_score >= 0.58:
            selection_label = "WATCHLIST_WORTHY"
        elif selection_score <= 0.40:
            selection_label = "AVOID"
        else:
            selection_label = "MIXED"
        notes = [
            *fundamental.notes,
            *event.notes,
            *sector_strength.notes,
            f"Unified stock-selection score is {selection_score:.2f} ({selection_label.lower().replace('_', ' ')}).",
        ]
        return StockIntelligence(
            symbol=symbol,
            sector=fundamental.sector,
            fundamental=fundamental,
            event=event,
            sector_strength=sector_strength,
            scoring_fundamental_score=scoring_fundamental_score,
            selection_score=selection_score,
            business_outlook_score=fundamental.outlook_score,
            valuation_score=fundamental.valuation_score,
            valuation_label=fundamental.valuation_label,
            selection_label=selection_label,
            combined_news_score=event.combined_news_score,
            notes=notes[:10],
        )
