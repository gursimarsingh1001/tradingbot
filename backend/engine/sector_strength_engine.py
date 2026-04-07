from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from backend.data.historical_fetcher import SymbolConfig
from backend.db.postgres import get_config_value, session_scope, upsert_config_value
from backend.engine.fundamental_engine import infer_sector_label


@dataclass(slots=True)
class SectorInsight:
    sector: str
    score: float
    label: str
    avg_return_20d: float
    breadth_above_sma50: float
    breadth_above_sma200: float
    peers: int
    notes: list[str]


class SectorStrengthEngine:
    CONFIG_KEY = "sector_strength_snapshot"

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        if parsed != parsed:
            return default
        return parsed

    def refresh_from_frames(self, symbol_configs: list[SymbolConfig], frame_map: dict[str, pd.DataFrame], *, generated_at: datetime) -> dict[str, Any]:
        sector_members: dict[str, list[dict[str, float]]] = {}
        symbol_sector_map: dict[str, str] = {}

        for symbol_config in symbol_configs:
            frame = frame_map.get(symbol_config.symbol)
            if frame is None or frame.empty:
                continue
            latest = frame.iloc[-1]
            if len(frame) < 50:
                continue
            sector = infer_sector_label(symbol_config.symbol, symbol_config.company_name, getattr(symbol_config, "sector", None))
            close = self._safe_float(latest.get("Close"), 0.0)
            ret_20d = 0.0
            if len(frame) > 20:
                past_close = self._safe_float(frame["Close"].iloc[-21], close)
                if past_close:
                    ret_20d = ((close / past_close) - 1.0) * 100.0
            sector_members.setdefault(sector, []).append(
                {
                    "return_20d": ret_20d,
                    "above_sma50": 1.0 if close > self._safe_float(latest.get("SMA_50"), close) else 0.0,
                    "above_sma200": 1.0 if close > self._safe_float(latest.get("SMA_200"), close) else 0.0,
                }
            )
            symbol_sector_map[symbol_config.symbol] = sector

        sector_snapshot: dict[str, Any] = {}
        for sector, members in sector_members.items():
            count = len(members)
            if count == 0:
                continue
            avg_return_20d = sum(item["return_20d"] for item in members) / count
            breadth_above_sma50 = sum(item["above_sma50"] for item in members) / count
            breadth_above_sma200 = sum(item["above_sma200"] for item in members) / count
            normalized_return = max(0.0, min(1.0, (avg_return_20d + 10.0) / 20.0))
            score = max(
                0.0,
                min(
                    1.0,
                    (normalized_return * 0.45) + (breadth_above_sma50 * 0.35) + (breadth_above_sma200 * 0.20),
                ),
            )
            if score >= 0.68:
                label = "STRONG"
            elif score <= 0.38:
                label = "WEAK"
            else:
                label = "NEUTRAL"
            sector_snapshot[sector] = {
                "score": round(score, 4),
                "label": label,
                "avg_return_20d": round(avg_return_20d, 4),
                "breadth_above_sma50": round(breadth_above_sma50, 4),
                "breadth_above_sma200": round(breadth_above_sma200, 4),
                "peers": count,
            }

        payload = {
            "generated_at": generated_at.isoformat(),
            "sectors": sector_snapshot,
            "symbols": symbol_sector_map,
        }
        with session_scope() as session:
            upsert_config_value(session, self.CONFIG_KEY, payload)
        return payload

    def _load_snapshot(self) -> dict[str, Any]:
        with session_scope() as session:
            return get_config_value(session, self.CONFIG_KEY, {"generated_at": None, "sectors": {}, "symbols": {}})

    def build_insight(self, symbol: str, company_name: str | None = None, explicit_sector: str | None = None) -> SectorInsight:
        snapshot = self._load_snapshot()
        sector = snapshot.get("symbols", {}).get(symbol) or infer_sector_label(symbol, company_name, explicit_sector)
        sector_row = snapshot.get("sectors", {}).get(sector, {})
        score = self._safe_float(sector_row.get("score"), 0.5)
        label = sector_row.get("label") or ("STRONG" if score >= 0.68 else "WEAK" if score <= 0.38 else "NEUTRAL")
        peers = int(sector_row.get("peers") or 0)
        avg_return_20d = self._safe_float(sector_row.get("avg_return_20d"))
        breadth_above_sma50 = self._safe_float(sector_row.get("breadth_above_sma50"), 0.5)
        breadth_above_sma200 = self._safe_float(sector_row.get("breadth_above_sma200"), 0.5)
        notes = [
            f"{sector} sector strength is {label.lower()} with score {score:.2f}.",
            f"Average 20-day peer return is {avg_return_20d:.2f}% across {peers} peers.",
            f"{breadth_above_sma50:.0%} of tracked peers are above their 50-day average.",
        ]
        return SectorInsight(
            sector=sector,
            score=score,
            label=label,
            avg_return_20d=avg_return_20d,
            breadth_above_sma50=breadth_above_sma50,
            breadth_above_sma200=breadth_above_sma200,
            peers=peers,
            notes=notes,
        )
