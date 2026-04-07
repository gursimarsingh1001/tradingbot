from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

from backend.config import get_settings


settings = get_settings()


@dataclass(frozen=True)
class HolidayEntry:
    trade_date: date
    description: str


class MarketCalendar:
    def __init__(self) -> None:
        self._loaded_from_path = None
        self._loaded_year = None
        self.segment = "NSE Capital Market"
        self.holidays: dict[date, str] = {}
        self.special_sessions: dict[date, str] = {}
        self._load()

    def _load(self) -> None:
        holidays_path = settings.market_holidays_path
        if holidays_path.exists():
            payload = json.loads(holidays_path.read_text(encoding="utf-8"))
        else:
            payload = {"segment": "NSE Capital Market", "tradingHolidays": [], "specialSessions": []}
        self.segment = payload.get("segment", "NSE Capital Market")
        self.holidays = {
            date.fromisoformat(item["date"]): item["description"]
            for item in payload.get("tradingHolidays", [])
        }
        self.special_sessions = {
            date.fromisoformat(item["date"]): item["description"]
            for item in payload.get("specialSessions", [])
        }
        self._loaded_from_path = holidays_path
        self._loaded_year = date.today().year

    def _ensure_fresh(self) -> None:
        current_year = date.today().year
        current_path = settings.market_holidays_path
        if self._loaded_year != current_year or self._loaded_from_path != current_path:
            self._load()

    @staticmethod
    def is_weekend(day: date) -> bool:
        return day.weekday() >= 5

    def holiday_name(self, day: date) -> str | None:
        self._ensure_fresh()
        return self.holidays.get(day)

    def is_trading_holiday(self, day: date) -> bool:
        self._ensure_fresh()
        return self.is_weekend(day) or day in self.holidays

    def is_trading_day(self, day: date) -> bool:
        return not self.is_trading_holiday(day)

    def closure_reason(self, day: date) -> str | None:
        if self.is_weekend(day):
            return f"Weekend closure ({day.strftime('%A')})"
        holiday = self.holiday_name(day)
        if holiday:
            return f"NSE trading holiday: {holiday}"
        return None

    def next_trading_day(self, from_day: date, *, include_same_day: bool = False) -> date:
        cursor = from_day if include_same_day else from_day + timedelta(days=1)
        while self.is_trading_holiday(cursor):
            cursor += timedelta(days=1)
        return cursor


@lru_cache(maxsize=1)
def get_market_calendar() -> MarketCalendar:
    return MarketCalendar()
