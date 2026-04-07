from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import isnan
from pathlib import Path
from statistics import median
from typing import Iterable

from sqlalchemy import and_, select

from backend.config import get_settings
from backend.db.postgres import NewsArticle, StockFundamentalSnapshot, session_scope


settings = get_settings()


SECTOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "BANKING": ("BANK", "NBFC", "FINANCE", "FINANCIERS", "CAPITAL", "HOUSING FINANCE", "MICROFINANCE", "INSURANCE", "AMC"),
    "IT": ("TECH", "SOFTWARE", "SYSTEMS", "INFOTECH", "CONSULTANCY", "DIGITAL", "COMPUTER", "SOLUTIONS"),
    "PHARMA": ("PHARMA", "LAB", "LABS", "HEALTH", "HOSPITAL", "MEDICAL", "BIO", "LIFE SCIENCE"),
    "AUTO": ("MOTOR", "MOTORS", "AUTO", "TYRE", "TRACTOR", "BATTERY", "VEHICLE", "MOTO"),
    "METALS": ("STEEL", "METAL", "ALUMIN", "COPPER", "ZINC", "MINING", "MINES"),
    "ENERGY": ("POWER", "GRID", "ENERGY", "OIL", "GAS", "PETRO", "ONGC", "COAL", "NTPC", "POWERGRID"),
    "UTILITIES": ("WATER", "TRANSMISSION", "UTILITY"),
    "REALTY": ("REALTY", "REAL ESTATE", "PROPERTIES", "HOUSING", "PROP", "DEVELOPERS"),
    "CEMENT": ("CEMENT", "CERAMIC", "TILES", "PIPES"),
    "FMCG": ("CONSUMER", "FOODS", "FOOD", "BEVERAGES", "BREW", "BREWERIES", "DAIRY", "SOAP", "PAINT", "CARE"),
    "TELECOM": ("TELECOM", "COMMUNICATION", "BROADBAND"),
    "INDUSTRIALS": ("ENGINEERING", "INDUSTRIES", "INFRA", "CONSTRUCTION", "LOGISTICS", "PORT", "AEROSPACE"),
    "CHEMICALS": ("CHEM", "CHEMICAL", "SPECIALITY", "FERTILIZER", "AGRO"),
    "TEXTILES": ("TEXTILE", "APPAREL", "GARMENTS", "FASHION"),
}

SYMBOL_SECTOR_OVERRIDES: dict[str, str] = {
    "TCS": "IT",
    "INFY": "IT",
    "WIPRO": "IT",
    "HCLTECH": "IT",
    "TECHM": "IT",
    "HDFCBANK": "BANKING",
    "ICICIBANK": "BANKING",
    "SBIN": "BANKING",
    "AXISBANK": "BANKING",
    "KOTAKBANK": "BANKING",
    "BAJFINANCE": "BANKING",
    "BAJAJFINSV": "BANKING",
    "HDFCLIFE": "BANKING",
    "SBILIFE": "BANKING",
    "ICICIPRULI": "BANKING",
    "SUNPHARMA": "PHARMA",
    "DRREDDY": "PHARMA",
    "CIPLA": "PHARMA",
    "DIVISLAB": "PHARMA",
    "TATAMOTORS": "AUTO",
    "MARUTI": "AUTO",
    "M&M": "AUTO",
    "BAJAJ-AUTO": "AUTO",
    "HEROMOTOCO": "AUTO",
    "TATASTEEL": "METALS",
    "JSWSTEEL": "METALS",
    "HINDALCO": "METALS",
    "ADANIPORTS": "INDUSTRIALS",
    "LT": "INDUSTRIALS",
    "ITC": "FMCG",
    "HINDUNILVR": "FMCG",
    "BRITANNIA": "FMCG",
    "NESTLEIND": "FMCG",
}

POSITIVE_FUNDAMENTAL_PATTERNS: dict[str, float] = {
    "profit rose": 0.25,
    "profit jumps": 0.25,
    "profit up": 0.20,
    "net profit": 0.15,
    "revenue rose": 0.18,
    "revenue grew": 0.18,
    "sales grew": 0.15,
    "ebitda margin expanded": 0.20,
    "margin expanded": 0.15,
    "debt reduced": 0.18,
    "deleveraging": 0.18,
    "order book": 0.12,
    "guidance raised": 0.22,
    "rating upgrade": 0.18,
    "promoter bought": 0.12,
    "buyback": 0.15,
}

NEGATIVE_FUNDAMENTAL_PATTERNS: dict[str, float] = {
    "loss widened": -0.30,
    "profit fell": -0.22,
    "profit declines": -0.22,
    "revenue declined": -0.18,
    "sales decline": -0.18,
    "margin contracted": -0.18,
    "ebitda margin contracted": -0.20,
    "debt rises": -0.18,
    "debt increased": -0.18,
    "downgrade": -0.20,
    "guidance cut": -0.24,
    "weak guidance": -0.24,
    "pledge": -0.15,
    "default": -0.30,
}


def infer_sector_label(symbol: str, company_name: str | None = None, explicit_sector: str | None = None) -> str:
    if explicit_sector:
        return explicit_sector.upper().strip()

    normalized_symbol = symbol.upper().strip()
    if normalized_symbol in SYMBOL_SECTOR_OVERRIDES:
        return SYMBOL_SECTOR_OVERRIDES[normalized_symbol]

    haystacks = [normalized_symbol]
    if company_name:
        haystacks.append(company_name.upper())
    combined = " ".join(haystacks)
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(keyword in combined for keyword in keywords):
            return sector
    return "DIVERSIFIED"


@dataclass(slots=True)
class FundamentalInsight:
    symbol: str
    sector: str
    score: float
    confidence: float
    has_snapshot: bool
    days_to_earnings: int | None
    earnings_risk: str | None
    flags: list[str]
    notes: list[str]
    business_quality_score: float
    growth_score: float
    balance_sheet_score: float
    valuation_score: float
    ownership_score: float
    outlook_score: float
    valuation_label: str
    sector_peer_count: int
    sector_medians: dict[str, float | None]
    selection_summary: str
    raw_metrics: dict[str, float | None]


@dataclass(slots=True)
class SnapshotScoreBreakdown:
    total: float
    business_quality_score: float
    growth_score: float
    balance_sheet_score: float
    valuation_score: float
    ownership_score: float
    outlook_score: float
    valuation_label: str
    sector_peer_count: int
    sector_medians: dict[str, float | None]
    selection_summary: str
    flags: list[str]
    notes: list[str]


def _safe_float(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if isnan(parsed):
        return None
    return parsed


def _bounded(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _scaled(value: float | None, *, low: float, high: float, inverse: bool = False) -> float | None:
    if value is None:
        return None
    if high == low:
        return 0.5
    normalized = (value - low) / (high - low)
    normalized = _bounded(normalized)
    return 1.0 - normalized if inverse else normalized


def _average(values: Iterable[float | None]) -> float | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _weighted_average(values: Iterable[tuple[float | None, float]]) -> float | None:
    weighted_sum = 0.0
    total_weight = 0.0
    for value, weight in values:
        if value is None or weight <= 0:
            continue
        weighted_sum += value * weight
        total_weight += weight
    if total_weight == 0:
        return None
    return weighted_sum / total_weight


class FundamentalEngine:
    PEER_CACHE_TTL = timedelta(minutes=30)

    def __init__(self) -> None:
        self._peer_cache_generated_at: datetime | None = None
        self._peer_cache: dict[str, dict[str, float | int | None]] = {}

    def invalidate_cache(self) -> None:
        self._peer_cache_generated_at = None
        self._peer_cache = {}

    def sync_from_config(self, path: Path | None = None) -> int:
        path = path or settings.fundamentals_config_path
        if not path.exists():
            return 0
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return 0

        synced = 0
        with session_scope() as session:
            for item in payload:
                if not isinstance(item, dict):
                    continue
                symbol = str(item.get("symbol") or "").upper().strip()
                if not symbol:
                    continue
                as_of_raw = item.get("asOfDate") or item.get("as_of_date")
                if not as_of_raw:
                    continue
                as_of_date = date.fromisoformat(str(as_of_raw))
                existing = session.scalar(
                    select(StockFundamentalSnapshot).where(
                        StockFundamentalSnapshot.symbol == symbol,
                        StockFundamentalSnapshot.as_of_date == as_of_date,
                    )
                )
                values = {
                    "company_name": item.get("companyName") or item.get("company_name"),
                    "sector": item.get("sector"),
                    "earnings_date": date.fromisoformat(str(item["earningsDate"])) if item.get("earningsDate") else (
                        date.fromisoformat(str(item["earnings_date"])) if item.get("earnings_date") else None
                    ),
                    "revenue_growth_pct": _safe_float(item.get("revenueGrowthPct") if item.get("revenueGrowthPct") is not None else item.get("revenue_growth_pct")),
                    "profit_growth_pct": _safe_float(item.get("profitGrowthPct") if item.get("profitGrowthPct") is not None else item.get("profit_growth_pct")),
                    "roe": _safe_float(item.get("roe")),
                    "roce": _safe_float(item.get("roce")),
                    "debt_to_equity": _safe_float(item.get("debtToEquity") if item.get("debtToEquity") is not None else item.get("debt_to_equity")),
                    "current_ratio": _safe_float(item.get("currentRatio") if item.get("currentRatio") is not None else item.get("current_ratio")),
                    "operating_margin": _safe_float(item.get("operatingMargin") if item.get("operatingMargin") is not None else item.get("operating_margin")),
                    "net_margin": _safe_float(item.get("netMargin") if item.get("netMargin") is not None else item.get("net_margin")),
                    "promoter_holding": _safe_float(item.get("promoterHolding") if item.get("promoterHolding") is not None else item.get("promoter_holding")),
                    "pledged_pct": _safe_float(item.get("pledgedPct") if item.get("pledgedPct") is not None else item.get("pledged_pct")),
                    "pe_ratio": _safe_float(item.get("peRatio") if item.get("peRatio") is not None else item.get("pe_ratio")),
                    "pb_ratio": _safe_float(item.get("pbRatio") if item.get("pbRatio") is not None else item.get("pb_ratio")),
                    "dividend_yield": _safe_float(item.get("dividendYield") if item.get("dividendYield") is not None else item.get("dividend_yield")),
                    "source": item.get("source") or "config",
                    "notes": item.get("notes"),
                }
                if existing is None:
                    session.add(
                        StockFundamentalSnapshot(
                            symbol=symbol,
                            as_of_date=as_of_date,
                            **values,
                        )
                    )
                else:
                    for key, value in values.items():
                        setattr(existing, key, value)
                synced += 1
        self.invalidate_cache()
        return synced

    def _latest_snapshots(self) -> list[StockFundamentalSnapshot]:
        with session_scope() as session:
            rows = session.scalars(
                select(StockFundamentalSnapshot).order_by(
                    StockFundamentalSnapshot.symbol.asc(),
                    StockFundamentalSnapshot.as_of_date.desc(),
                    StockFundamentalSnapshot.created_at.desc(),
                )
            ).all()
        latest_by_symbol: dict[str, StockFundamentalSnapshot] = {}
        for row in rows:
            if row.symbol not in latest_by_symbol:
                latest_by_symbol[row.symbol] = row
        return list(latest_by_symbol.values())

    @staticmethod
    def _median(values: Iterable[float | None]) -> float | None:
        clean = [value for value in values if value is not None]
        if not clean:
            return None
        return float(median(clean))

    def _load_sector_peer_snapshot(self) -> dict[str, dict[str, float | int | None]]:
        now = datetime.now(tz=settings.tzinfo)
        if self._peer_cache_generated_at and now - self._peer_cache_generated_at <= self.PEER_CACHE_TTL:
            return self._peer_cache

        sector_members: dict[str, list[StockFundamentalSnapshot]] = {}
        for snapshot in self._latest_snapshots():
            sector = infer_sector_label(snapshot.symbol, snapshot.company_name, snapshot.sector)
            sector_members.setdefault(sector, []).append(snapshot)

        peer_snapshot: dict[str, dict[str, float | int | None]] = {}
        for sector, members in sector_members.items():
            peer_snapshot[sector] = {
                "peers": len(members),
                "revenue_growth_pct": self._median(_safe_float(member.revenue_growth_pct) for member in members),
                "profit_growth_pct": self._median(_safe_float(member.profit_growth_pct) for member in members),
                "roe": self._median(_safe_float(member.roe) for member in members),
                "roce": self._median(_safe_float(member.roce) for member in members),
                "debt_to_equity": self._median(_safe_float(member.debt_to_equity) for member in members),
                "current_ratio": self._median(_safe_float(member.current_ratio) for member in members),
                "operating_margin": self._median(_safe_float(member.operating_margin) for member in members),
                "net_margin": self._median(_safe_float(member.net_margin) for member in members),
                "promoter_holding": self._median(_safe_float(member.promoter_holding) for member in members),
                "pledged_pct": self._median(_safe_float(member.pledged_pct) for member in members),
                "pe_ratio": self._median(_safe_float(member.pe_ratio) for member in members),
                "pb_ratio": self._median(_safe_float(member.pb_ratio) for member in members),
                "dividend_yield": self._median(_safe_float(member.dividend_yield) for member in members),
            }

        self._peer_cache_generated_at = now
        self._peer_cache = peer_snapshot
        return peer_snapshot

    @staticmethod
    def _relative_score(value: float | None, sector_median: float | None, *, inverse: bool = False) -> float | None:
        if value is None or sector_median is None:
            return None
        if sector_median == 0:
            return None
        ratio = value / sector_median
        if inverse:
            return _bounded(0.5 + ((1.0 - ratio) * 0.5))
        return _bounded(0.5 + ((ratio - 1.0) * 0.5))

    def _latest_snapshot(self, symbol: str) -> StockFundamentalSnapshot | None:
        with session_scope() as session:
            return session.scalar(
                select(StockFundamentalSnapshot)
                .where(StockFundamentalSnapshot.symbol == symbol)
                .order_by(StockFundamentalSnapshot.as_of_date.desc(), StockFundamentalSnapshot.created_at.desc())
            )

    def _recent_financial_articles(self, symbol: str, as_of: datetime) -> list[NewsArticle]:
        cutoff = as_of - timedelta(days=90)
        with session_scope() as session:
            return session.scalars(
                select(NewsArticle)
                .where(
                    and_(
                        NewsArticle.symbol == symbol,
                        NewsArticle.published_at.is_not(None),
                        NewsArticle.published_at >= cutoff,
                        NewsArticle.published_at <= as_of,
                    )
                )
                .order_by(NewsArticle.published_at.desc())
                .limit(30)
            ).all()

    def _score_snapshot(self, snapshot: StockFundamentalSnapshot) -> SnapshotScoreBreakdown | None:
        flags: list[str] = []
        sector = infer_sector_label(snapshot.symbol, snapshot.company_name, snapshot.sector)
        sector_stats = self._load_sector_peer_snapshot().get(sector, {})
        sector_medians = {
            "revenue_growth_pct": _safe_float(sector_stats.get("revenue_growth_pct")),
            "profit_growth_pct": _safe_float(sector_stats.get("profit_growth_pct")),
            "roe": _safe_float(sector_stats.get("roe")),
            "roce": _safe_float(sector_stats.get("roce")),
            "debt_to_equity": _safe_float(sector_stats.get("debt_to_equity")),
            "current_ratio": _safe_float(sector_stats.get("current_ratio")),
            "operating_margin": _safe_float(sector_stats.get("operating_margin")),
            "net_margin": _safe_float(sector_stats.get("net_margin")),
            "promoter_holding": _safe_float(sector_stats.get("promoter_holding")),
            "pledged_pct": _safe_float(sector_stats.get("pledged_pct")),
            "pe_ratio": _safe_float(sector_stats.get("pe_ratio")),
            "pb_ratio": _safe_float(sector_stats.get("pb_ratio")),
            "dividend_yield": _safe_float(sector_stats.get("dividend_yield")),
        }
        growth_absolute = _average(
            [
                _scaled(_safe_float(snapshot.revenue_growth_pct), low=-10.0, high=25.0),
                _scaled(_safe_float(snapshot.profit_growth_pct), low=-15.0, high=30.0),
            ]
        )
        growth_relative = _average(
            [
                self._relative_score(_safe_float(snapshot.revenue_growth_pct), sector_medians["revenue_growth_pct"]),
                self._relative_score(_safe_float(snapshot.profit_growth_pct), sector_medians["profit_growth_pct"]),
            ]
        )
        growth_score = _average([growth_absolute, growth_relative])

        returns_absolute = _average(
            [
                _scaled(_safe_float(snapshot.roe), low=8.0, high=22.0),
                _scaled(_safe_float(snapshot.roce), low=8.0, high=24.0),
            ]
        )
        returns_relative = _average(
            [
                self._relative_score(_safe_float(snapshot.roe), sector_medians["roe"]),
                self._relative_score(_safe_float(snapshot.roce), sector_medians["roce"]),
            ]
        )
        returns_score = _average([returns_absolute, returns_relative])

        balance_absolute = _average(
            [
                _scaled(_safe_float(snapshot.debt_to_equity), low=0.0, high=1.5, inverse=True),
                _scaled(_safe_float(snapshot.current_ratio), low=1.0, high=2.5),
                _scaled(_safe_float(snapshot.pledged_pct), low=0.0, high=10.0, inverse=True),
            ]
        )
        balance_relative = _average(
            [
                self._relative_score(_safe_float(snapshot.debt_to_equity), sector_medians["debt_to_equity"], inverse=True),
                self._relative_score(_safe_float(snapshot.current_ratio), sector_medians["current_ratio"]),
                self._relative_score(_safe_float(snapshot.pledged_pct), sector_medians["pledged_pct"], inverse=True),
            ]
        )
        balance_sheet_score = _average([balance_absolute, balance_relative])

        margins_absolute = _average(
            [
                _scaled(_safe_float(snapshot.operating_margin), low=8.0, high=28.0),
                _scaled(_safe_float(snapshot.net_margin), low=4.0, high=20.0),
            ]
        )
        margins_relative = _average(
            [
                self._relative_score(_safe_float(snapshot.operating_margin), sector_medians["operating_margin"]),
                self._relative_score(_safe_float(snapshot.net_margin), sector_medians["net_margin"]),
            ]
        )
        margins_score = _average([margins_absolute, margins_relative])
        business_quality_score = _average([returns_score, margins_score])

        valuation_absolute = _average(
            [
                _scaled(_safe_float(snapshot.pe_ratio), low=8.0, high=35.0, inverse=True),
                _scaled(_safe_float(snapshot.pb_ratio), low=1.0, high=6.0, inverse=True),
                _scaled(_safe_float(snapshot.dividend_yield), low=0.0, high=3.5),
            ]
        )
        valuation_relative = _average(
            [
                self._relative_score(_safe_float(snapshot.pe_ratio), sector_medians["pe_ratio"], inverse=True),
                self._relative_score(_safe_float(snapshot.pb_ratio), sector_medians["pb_ratio"], inverse=True),
                self._relative_score(_safe_float(snapshot.dividend_yield), sector_medians["dividend_yield"]),
            ]
        )
        valuation_score = _average([valuation_absolute, valuation_relative])

        ownership_score = _average(
            [
                _scaled(_safe_float(snapshot.promoter_holding), low=35.0, high=75.0),
                self._relative_score(_safe_float(snapshot.promoter_holding), sector_medians["promoter_holding"]),
                _scaled(_safe_float(snapshot.pledged_pct), low=0.0, high=10.0, inverse=True),
            ]
        )
        outlook_score = _average([growth_score, business_quality_score, balance_sheet_score])

        score = _weighted_average(
            [
                (growth_score, 0.22),
                (business_quality_score, 0.24),
                (balance_sheet_score, 0.18),
                (valuation_score, 0.18),
                (ownership_score, 0.08),
                (outlook_score, 0.10),
            ]
        )
        if score is None:
            return None

        if _safe_float(snapshot.debt_to_equity) is not None and float(snapshot.debt_to_equity or 0.0) > 1.0:
            flags.append("High leverage on the latest stored balance-sheet snapshot.")
        if _safe_float(snapshot.pledged_pct) is not None and float(snapshot.pledged_pct or 0.0) > 3.0:
            flags.append("Promoter pledge is elevated in the latest stored snapshot.")
        if _safe_float(snapshot.profit_growth_pct) is not None and float(snapshot.profit_growth_pct or 0.0) < 0:
            flags.append("Latest stored profit growth is negative.")
        if _safe_float(snapshot.roe) is not None and float(snapshot.roe or 0.0) >= 18.0:
            flags.append("Return on equity is strong on the latest stored numbers.")
        if _safe_float(snapshot.pe_ratio) is not None and sector_medians["pe_ratio"] is not None:
            pe_ratio = float(snapshot.pe_ratio or 0.0)
            sector_pe = float(sector_medians["pe_ratio"] or 0.0)
            if pe_ratio > 0 and sector_pe > 0:
                if pe_ratio <= sector_pe * 0.85:
                    flags.append(f"PE ratio at {pe_ratio:.1f} is below the sector median of {sector_pe:.1f}.")
                elif pe_ratio >= sector_pe * 1.20:
                    flags.append(f"PE ratio at {pe_ratio:.1f} is rich versus the sector median of {sector_pe:.1f}.")
        if _safe_float(snapshot.pb_ratio) is not None and sector_medians["pb_ratio"] is not None:
            pb_ratio = float(snapshot.pb_ratio or 0.0)
            sector_pb = float(sector_medians["pb_ratio"] or 0.0)
            if pb_ratio > 0 and sector_pb > 0:
                if pb_ratio <= sector_pb * 0.85:
                    flags.append(f"PB ratio at {pb_ratio:.1f} is below the sector median of {sector_pb:.1f}.")
                elif pb_ratio >= sector_pb * 1.20:
                    flags.append(f"PB ratio at {pb_ratio:.1f} is rich versus the sector median of {sector_pb:.1f}.")

        if valuation_score is not None and valuation_score >= 0.68:
            valuation_label = "CHEAP"
        elif valuation_score is not None and valuation_score <= 0.38:
            valuation_label = "EXPENSIVE"
        else:
            valuation_label = "FAIR"

        if score >= 0.72 and valuation_label == "CHEAP":
            selection_summary = "High-quality business trading at a relatively attractive valuation."
        elif score >= 0.72:
            selection_summary = "High-quality business with healthy fundamentals, though valuation needs monitoring."
        elif valuation_label == "CHEAP" and outlook_score is not None and outlook_score >= 0.55:
            selection_summary = "Reasonably priced stock with improving medium-term outlook."
        elif valuation_label == "EXPENSIVE" and business_quality_score is not None and business_quality_score < 0.55:
            selection_summary = "Valuation looks stretched relative to the underlying business quality."
        else:
            selection_summary = "Balanced setup with mixed quality and valuation signals."

        sector_peer_count = int(sector_stats.get("peers") or 0)
        notes = [
            selection_summary,
            f"{sector} peer set has {sector_peer_count} structured snapshots for sector-relative comparison.",
        ]
        if valuation_label == "CHEAP":
            notes.append("Valuation looks better than the typical sector stock on the stored snapshot.")
        elif valuation_label == "EXPENSIVE":
            notes.append("Valuation is richer than the typical sector stock on the stored snapshot.")
        return SnapshotScoreBreakdown(
            total=_bounded(score),
            business_quality_score=_bounded(business_quality_score or 0.5),
            growth_score=_bounded(growth_score or 0.5),
            balance_sheet_score=_bounded(balance_sheet_score or 0.5),
            valuation_score=_bounded(valuation_score or 0.5),
            ownership_score=_bounded(ownership_score or 0.5),
            outlook_score=_bounded(outlook_score or 0.5),
            valuation_label=valuation_label,
            sector_peer_count=sector_peer_count,
            sector_medians=sector_medians,
            selection_summary=selection_summary,
            flags=flags,
            notes=notes,
        )

    def _score_financial_news(self, symbol: str, as_of: datetime) -> tuple[float | None, list[str]]:
        articles = self._recent_financial_articles(symbol, as_of)
        if not articles:
            return None, []

        total_weight = 0.0
        weighted_score = 0.0
        flags: list[str] = []
        for article in articles:
            text = f"{article.headline or ''} {article.body_snippet or ''}".lower()
            age_days = max((as_of - (article.published_at or as_of)).days, 0)
            recency = max(0.25, 1.0 - (age_days / 120.0))
            article_score = 0.0
            for pattern, weight in POSITIVE_FUNDAMENTAL_PATTERNS.items():
                if pattern in text:
                    article_score += weight
                    if len(flags) < 6:
                        flags.append(f"Recent news highlighted a positive financial trigger: '{pattern}'.")
            for pattern, weight in NEGATIVE_FUNDAMENTAL_PATTERNS.items():
                if pattern in text:
                    article_score += weight
                    if len(flags) < 6:
                        flags.append(f"Recent news highlighted a financial risk: '{pattern}'.")
            if article_score == 0.0:
                article_score += _bounded((float(article.sentiment_score or 0.0) + 1.0) / 2.0, 0.0, 1.0) - 0.5
            weighted_score += article_score * recency
            total_weight += recency

        if total_weight == 0:
            return None, flags

        normalized = _bounded((weighted_score / total_weight) + 0.5, 0.0, 1.0)
        return normalized, flags

    def build_insight(self, symbol: str, company_name: str | None, as_of: datetime) -> FundamentalInsight:
        snapshot = self._latest_snapshot(symbol)
        sector = infer_sector_label(symbol, company_name, snapshot.sector if snapshot else None)
        snapshot_breakdown = self._score_snapshot(snapshot) if snapshot else None
        snapshot_score = snapshot_breakdown.total if snapshot_breakdown else None
        snapshot_flags = snapshot_breakdown.flags if snapshot_breakdown else []
        news_score, news_flags = self._score_financial_news(symbol, as_of)

        flags = [*snapshot_flags, *news_flags]
        if snapshot_score is not None and news_score is not None:
            score = (snapshot_score * 0.75) + (news_score * 0.25)
            confidence = 0.9 if snapshot_breakdown and snapshot_breakdown.sector_peer_count >= 5 else 0.8
        elif snapshot_score is not None:
            score = snapshot_score
            confidence = 0.8 if snapshot_breakdown and snapshot_breakdown.sector_peer_count >= 5 else 0.68
        elif news_score is not None:
            score = (0.5 * 0.35) + (news_score * 0.65)
            confidence = 0.45
            flags.append("No structured fundamentals were stored, so financial-news momentum carried more weight.")
        else:
            score = 0.5
            confidence = 0.2
            flags.append("No structured fundamentals or financial-news cues were available, so the score stayed neutral.")

        days_to_earnings: int | None = None
        earnings_risk: str | None = None
        if snapshot and snapshot.earnings_date:
            target_day = snapshot.earnings_date if isinstance(snapshot.earnings_date, date) else snapshot.earnings_date.date()
            days_to_earnings = (target_day - as_of.date()).days
            if days_to_earnings <= 1:
                earnings_risk = "IMMINENT"
                flags.append("Earnings are due today or by the next trading session.")
            elif days_to_earnings <= 5:
                earnings_risk = "NEAR"
                flags.append("Earnings are approaching within the next few sessions.")

        notes = [
            f"Fundamental quality score {score:.2f} for {sector}.",
            f"Confidence in the fundamental read is {confidence:.2f}.",
        ]
        if snapshot_breakdown is not None:
            notes.extend(snapshot_breakdown.notes[:3])
        raw_metrics = {
            "revenue_growth_pct": _safe_float(snapshot.revenue_growth_pct) if snapshot else None,
            "profit_growth_pct": _safe_float(snapshot.profit_growth_pct) if snapshot else None,
            "roe": _safe_float(snapshot.roe) if snapshot else None,
            "roce": _safe_float(snapshot.roce) if snapshot else None,
            "debt_to_equity": _safe_float(snapshot.debt_to_equity) if snapshot else None,
            "current_ratio": _safe_float(snapshot.current_ratio) if snapshot else None,
            "operating_margin": _safe_float(snapshot.operating_margin) if snapshot else None,
            "net_margin": _safe_float(snapshot.net_margin) if snapshot else None,
            "promoter_holding": _safe_float(snapshot.promoter_holding) if snapshot else None,
            "pledged_pct": _safe_float(snapshot.pledged_pct) if snapshot else None,
            "pe_ratio": _safe_float(snapshot.pe_ratio) if snapshot else None,
            "pb_ratio": _safe_float(snapshot.pb_ratio) if snapshot else None,
            "dividend_yield": _safe_float(snapshot.dividend_yield) if snapshot else None,
        }
        return FundamentalInsight(
            symbol=symbol,
            sector=sector,
            score=_bounded(score),
            confidence=confidence,
            has_snapshot=bool(snapshot),
            days_to_earnings=days_to_earnings,
            earnings_risk=earnings_risk,
            flags=flags[:8],
            notes=notes,
            business_quality_score=snapshot_breakdown.business_quality_score if snapshot_breakdown else 0.5,
            growth_score=snapshot_breakdown.growth_score if snapshot_breakdown else 0.5,
            balance_sheet_score=snapshot_breakdown.balance_sheet_score if snapshot_breakdown else 0.5,
            valuation_score=snapshot_breakdown.valuation_score if snapshot_breakdown else 0.5,
            ownership_score=snapshot_breakdown.ownership_score if snapshot_breakdown else 0.5,
            outlook_score=snapshot_breakdown.outlook_score if snapshot_breakdown else 0.5,
            valuation_label=snapshot_breakdown.valuation_label if snapshot_breakdown else "FAIR",
            sector_peer_count=snapshot_breakdown.sector_peer_count if snapshot_breakdown else 0,
            sector_medians=snapshot_breakdown.sector_medians if snapshot_breakdown else {},
            selection_summary=snapshot_breakdown.selection_summary if snapshot_breakdown else "No structured valuation snapshot was available.",
            raw_metrics=raw_metrics,
        )
