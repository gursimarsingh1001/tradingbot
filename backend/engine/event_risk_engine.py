from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, select

from backend.config import get_settings
from backend.db.postgres import NewsArticle, session_scope


settings = get_settings()

EVENT_RULES: tuple[tuple[str, tuple[str, ...], float, str, bool], ...] = (
    ("MANAGEMENT_EXIT", ("ceo resign", "cfo resign", "director resign", "auditor resign", "md resign"), -1.0, "Management exit", True),
    ("EARNINGS_BEAT", ("profit jumps", "beats estimates", "above estimates", "margin expands", "record profit"), 0.85, "Earnings beat", False),
    ("EARNINGS_MISS", ("misses estimates", "below estimates", "profit falls", "loss widens", "margin contracts"), -0.9, "Earnings miss", True),
    ("GUIDANCE_UP", ("guidance raised", "raises guidance", "upbeat guidance"), 0.7, "Guidance upgrade", False),
    ("GUIDANCE_DOWN", ("guidance cut", "cuts guidance", "weak guidance"), -0.8, "Guidance cut", True),
    ("ORDER_WIN", ("wins order", "wins contract", "receives order", "order win"), 0.65, "Large order win", False),
    ("REGULATORY_APPROVAL", ("gets approval", "receives approval", "clearance granted"), 0.6, "Regulatory approval", False),
    ("REGULATORY_RISK", ("show cause", "probe", "investigation", "penalty", "regulatory action", "tax notice"), -0.75, "Regulatory risk", True),
    ("PROMOTER_BUY", ("promoter bought", "promoter increases stake", "stake purchase"), 0.45, "Promoter buying", False),
    ("PROMOTER_SELL", ("promoter sells", "stake sale", "block deal", "ofs"), -0.45, "Promoter selling", False),
    ("PLEDGE_RISK", ("pledge", "pledged shares"), -0.5, "Promoter pledge risk", True),
    ("BUYBACK", ("buyback", "share buyback"), 0.55, "Buyback support", False),
    ("DIVIDEND", ("dividend", "special dividend"), 0.35, "Dividend support", False),
    ("RESULTS_EVENT", ("q1 results", "q2 results", "q3 results", "q4 results", "earnings today", "board meeting"), 0.0, "Results event risk", True),
)

RESULTS_CONTEXT_TERMS = (
    "q1",
    "q2",
    "q3",
    "q4",
    "quarter",
    "results",
    "earnings",
    "financial",
    "profit",
    "revenue",
    "sales",
    "margin",
    "ebitda",
    "pat",
)
POSITIVE_MARGIN_TERMS = ("margin expands", "margin expansion", "record profit", "highest ever", "all-time high profit")
PERCENT_TOKEN = r"(?:%|percent|per\s+cent)"
PROFIT_PATTERNS = (
    rf"(?:net\s+profit|profit|pat)[^.%]{{0,70}}?(?:up|rose|rises|jumped|jumps|surged|surges|grew|grows|growth(?:\s+of)?|increased|climbed)\s*(\d+(?:\.\d+)?)\s*{PERCENT_TOKEN}",
    rf"(?:net\s+profit|profit|pat)[^.%]{{0,50}}?(\d+(?:\.\d+)?)\s*{PERCENT_TOKEN}\s*(?:up|higher|growth|jump|rise|surge)",
)
REVENUE_PATTERNS = (
    rf"(?:revenue|sales|income|turnover)[^.%]{{0,70}}?(?:up|rose|rises|jumped|jumps|surged|surges|grew|grows|growth(?:\s+of)?|increased|climbed)\s*(\d+(?:\.\d+)?)\s*{PERCENT_TOKEN}",
    rf"(?:revenue|sales|income|turnover)[^.%]{{0,50}}?(\d+(?:\.\d+)?)\s*{PERCENT_TOKEN}\s*(?:up|higher|growth|jump|rise|surge)",
)


@dataclass(slots=True)
class FinancialCatalystInsight:
    score: float
    is_positive: bool
    results_context: bool
    profit_growth_pct: float | None
    revenue_growth_pct: float | None
    flags: list[str]
    summary: str | None


def _extract_percent(text: str, patterns: tuple[str, ...]) -> float | None:
    values: list[float] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            try:
                values.append(float(match.group(1)))
            except (TypeError, ValueError):
                continue
    return max(values) if values else None


def extract_financial_catalyst(text: str) -> FinancialCatalystInsight:
    lowered = text.lower()
    results_context = any(term in lowered for term in RESULTS_CONTEXT_TERMS)
    profit_growth_pct = _extract_percent(lowered, PROFIT_PATTERNS)
    revenue_growth_pct = _extract_percent(lowered, REVENUE_PATTERNS)
    if profit_growth_pct is None and re.search(r"(?:net\s+profit|profit|pat)[^.]*(?:double|doubles|doubled)", lowered):
        profit_growth_pct = 100.0

    score = 0.0
    flags: list[str] = []

    if results_context and profit_growth_pct is not None:
        if profit_growth_pct >= settings.news_financial_profit_surge_pct:
            score += 0.75
        elif profit_growth_pct >= max(25.0, settings.news_financial_profit_surge_pct * 0.4):
            score += 0.40
        flags.append(f"Profit growth +{profit_growth_pct:.0f}%")

    if results_context and revenue_growth_pct is not None:
        if revenue_growth_pct >= settings.news_financial_revenue_surge_pct:
            score += 0.35
        elif revenue_growth_pct >= max(8.0, settings.news_financial_revenue_surge_pct * 0.5):
            score += 0.18
        flags.append(f"Revenue growth +{revenue_growth_pct:.0f}%")

    if results_context and any(term in lowered for term in POSITIVE_MARGIN_TERMS):
        score += 0.15
        flags.append("Margin expansion")

    if results_context and score >= settings.news_financial_catalyst_score_threshold:
        flags.insert(0, "Fresh results catalyst")

    score = max(0.0, min(1.0, score))
    summary = None
    if score >= settings.news_financial_catalyst_score_threshold:
        summary = "Fresh financial results are strongly positive and can support an intraday long watch."
    return FinancialCatalystInsight(
        score=score,
        is_positive=score >= settings.news_financial_catalyst_score_threshold,
        results_context=results_context,
        profit_growth_pct=profit_growth_pct,
        revenue_growth_pct=revenue_growth_pct,
        flags=flags[:4],
        summary=summary,
    )


def classify_event_labels(text: str) -> list[str]:
    lowered = text.lower()
    labels: list[str] = []
    for _event_type, patterns, _impact, label, _is_high in EVENT_RULES:
        if any(pattern in lowered for pattern in patterns) and label not in labels:
            labels.append(label)
    catalyst = extract_financial_catalyst(lowered)
    for flag in catalyst.flags:
        if flag not in labels:
            labels.append(flag)
    return labels


@dataclass(slots=True)
class EventInsight:
    symbol: str
    sentiment_score: float
    event_score: float
    combined_news_score: float
    event_flags: list[str]
    high_impact_negative: bool
    high_impact_positive: bool
    has_results_event: bool
    positive_results_catalyst: bool
    financial_catalyst_score: float
    profit_growth_pct: float | None
    revenue_growth_pct: float | None
    catalyst_summary: str | None
    notes: list[str]


class EventRiskEngine:
    def _recent_articles(self, symbol: str, as_of: datetime, *, signal_type: str) -> list[NewsArticle]:
        lookback = timedelta(hours=36 if signal_type == "INTRADAY" else 120)
        with session_scope() as session:
            return session.scalars(
                select(NewsArticle)
                .where(
                    and_(
                        NewsArticle.symbol == symbol,
                        NewsArticle.published_at.is_not(None),
                        NewsArticle.published_at >= as_of - lookback,
                        NewsArticle.published_at <= as_of,
                    )
                )
                .order_by(NewsArticle.published_at.desc())
                .limit(25)
            ).all()

    def build_insight(
        self,
        symbol: str,
        *,
        as_of: datetime,
        signal_type: str,
        base_sentiment: float,
        days_to_earnings: int | None = None,
    ) -> EventInsight:
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=settings.tzinfo)
        articles = self._recent_articles(symbol, as_of, signal_type=signal_type)
        weighted_event_score = 0.0
        total_weight = 0.0
        event_flags: list[str] = []
        high_impact_negative = False
        high_impact_positive = False
        has_results_event = False
        positive_results_catalyst = False
        financial_catalyst_score = 0.0
        profit_growth_pct: float | None = None
        revenue_growth_pct: float | None = None
        catalyst_summary: str | None = None

        for article in articles:
            published_at = article.published_at or as_of
            age_hours = max((as_of - published_at).total_seconds() / 3600.0, 0.0)
            recency = max(0.25, 1.0 - (age_hours / (48.0 if signal_type == "INTRADAY" else 168.0)))
            text = f"{article.headline or ''} {article.body_snippet or ''}".lower()
            article_score = 0.0
            article_flags: list[str] = []
            for event_type, patterns, impact, label, is_high in EVENT_RULES:
                if any(pattern in text for pattern in patterns):
                    article_score += impact
                    article_flags.append(label)
                    if is_high and impact < 0:
                        high_impact_negative = True
                    if is_high and impact > 0:
                        high_impact_positive = True
                    if event_type == "RESULTS_EVENT":
                        has_results_event = True
            catalyst = extract_financial_catalyst(text)
            if catalyst.results_context:
                has_results_event = True
            moderate_positive_results = (
                catalyst.results_context
                and catalyst.score >= 0.30
                and float(article.sentiment_score or 0.0) >= 0.75
            )
            if catalyst.score > 0.0:
                article_score += catalyst.score
                catalyst_flags = list(catalyst.flags)
                if moderate_positive_results and "Fresh results catalyst" not in catalyst_flags:
                    catalyst_flags.insert(0, "Fresh results catalyst")
                for flag in catalyst.flags:
                    if flag not in article_flags:
                        article_flags.append(flag)
                if catalyst.is_positive or moderate_positive_results:
                    positive_results_catalyst = True
                    high_impact_positive = True
                if catalyst.score > financial_catalyst_score:
                    financial_catalyst_score = catalyst.score
                    profit_growth_pct = catalyst.profit_growth_pct
                    revenue_growth_pct = catalyst.revenue_growth_pct
                    catalyst_summary = catalyst.summary or (
                        "Fresh business/results update is positive enough to justify an intraday long watch."
                        if moderate_positive_results
                        else None
                    )
                for flag in catalyst_flags:
                    if flag not in article_flags:
                        article_flags.append(flag)
            if not article_flags:
                continue
            weighted_event_score += article_score * recency
            total_weight += recency
            for label in article_flags:
                if len(event_flags) < 10 and label not in event_flags:
                    event_flags.append(label)

        if days_to_earnings is not None and days_to_earnings <= 1:
            has_results_event = True
            high_impact_negative = True
            if "Earnings due by the next session" not in event_flags:
                event_flags.append("Earnings due by the next session")

        event_score = (weighted_event_score / total_weight) if total_weight else 0.0
        event_score = max(-1.0, min(1.0, event_score))
        combined_news_score = max(-5.0, min(5.0, (base_sentiment * 0.6) + (event_score * 4.0)))

        notes = [
            f"News sentiment blended to {combined_news_score:.2f} after event adjustment.",
        ]
        if event_flags:
            notes.append(f"Detected event context: {', '.join(event_flags[:4])}.")
        if catalyst_summary:
            notes.append(catalyst_summary)
        return EventInsight(
            symbol=symbol,
            sentiment_score=base_sentiment,
            event_score=event_score,
            combined_news_score=combined_news_score,
            event_flags=event_flags,
            high_impact_negative=high_impact_negative,
            high_impact_positive=high_impact_positive,
            has_results_event=has_results_event,
            positive_results_catalyst=positive_results_catalyst,
            financial_catalyst_score=financial_catalyst_score,
            profit_growth_pct=profit_growth_pct,
            revenue_growth_pct=revenue_growth_pct,
            catalyst_summary=catalyst_summary,
            notes=notes,
        )
