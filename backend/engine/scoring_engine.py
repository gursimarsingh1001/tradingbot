from __future__ import annotations

from threading import Lock
from time import monotonic

from sqlalchemy import select

from backend.config import get_settings
from backend.db.postgres import ScoringWeight, session_scope


DEFAULT_WEIGHTS = {
    "pattern": 0.28,
    "ma": 0.18,
    "volume": 0.16,
    "news": 0.14,
    "regime": 0.14,
    "fundament": 0.10,
}

settings = get_settings()


class ScoringEngine:
    def __init__(self) -> None:
        self._weights_lock = Lock()
        self._cached_weights: dict[str, float] | None = None
        self._cached_until: float = 0.0

    def _load_weights_from_db(self) -> dict[str, float]:
        with session_scope() as session:
            latest = session.scalar(select(ScoringWeight).order_by(ScoringWeight.created_at.desc()))
        if latest is None:
            return DEFAULT_WEIGHTS.copy()
        return {
            "pattern": latest.pattern_weight,
            "ma": latest.ma_weight,
            "volume": latest.volume_weight,
            "news": latest.news_weight,
            "regime": latest.regime_weight,
            "fundament": latest.fundamental_weight,
        }

    def load_current_weights(self, *, force_refresh: bool = False) -> dict[str, float]:
        now = monotonic()
        if not force_refresh and self._cached_weights is not None and now < self._cached_until:
            return self._cached_weights.copy()

        with self._weights_lock:
            now = monotonic()
            if not force_refresh and self._cached_weights is not None and now < self._cached_until:
                return self._cached_weights.copy()

            weights = self._load_weights_from_db()
            self._cached_weights = weights
            self._cached_until = now + max(settings.scoring_weights_ttl_seconds, 1)
            return weights.copy()

    def score(self, signal_data: dict) -> float:
        weights = self.load_current_weights()
        score = (
            signal_data["pattern_strength"] * weights["pattern"]
            + signal_data["ma_alignment"] * weights["ma"]
            + signal_data["volume_ratio"] * weights["volume"]
            + signal_data["news_score_norm"] * weights["news"]
            + signal_data["regime_match"] * weights["regime"]
            + signal_data["fundamental_score"] * weights["fundament"]
        )
        return min(100.0, max(0.0, score * 100))
