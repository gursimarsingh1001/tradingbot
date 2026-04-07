from __future__ import annotations

import re
import time
from datetime import date, datetime
from threading import Lock
from typing import Any, Iterable


def normalize_key(value: str) -> str:
    return "".join(char for char in value.lower() if char.isalnum())


def coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in {"na", "n/a", "none", "-", "--"}:
        return None
    multiplier = 1.0
    text = (
        text.replace(",", "")
        .replace("₹", "")
        .replace("rs.", "")
        .replace("rs", "")
        .replace("%", "")
        .strip()
    )
    lowered = text.lower()
    if lowered.endswith("cr"):
        multiplier = 10_000_000.0
        text = text[:-2].strip()
    elif lowered.endswith("crore"):
        multiplier = 10_000_000.0
        text = text[:-5].strip()
    elif lowered.endswith("lakh"):
        multiplier = 100_000.0
        text = text[:-4].strip()
    elif lowered.endswith("mn"):
        multiplier = 1_000_000.0
        text = text[:-2].strip()
    elif lowered.endswith("bn"):
        multiplier = 1_000_000_000.0
        text = text[:-2].strip()
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    cleaned = text.replace("/", "-")
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d-%b-%Y", "%d-%b-%y", "%b %d, %Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    match = re.search(r"\d{4}-\d{2}-\d{2}", cleaned)
    if match:
        try:
            return date.fromisoformat(match.group(0))
        except ValueError:
            return None
    return None


def iter_dict_records(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        if payload:
            yield payload
        for value in payload.values():
            yield from iter_dict_records(value)
        return
    if isinstance(payload, list):
        for item in payload:
            yield from iter_dict_records(item)


def deep_get_first(payload: Any, aliases: Iterable[str]) -> Any:
    alias_set = {normalize_key(alias) for alias in aliases}
    for record in iter_dict_records(payload):
        for key, value in record.items():
            if normalize_key(str(key)) in alias_set and value not in (None, ""):
                return value
    return None


def first_float(payload: Any, aliases: Iterable[str]) -> float | None:
    return coerce_float(deep_get_first(payload, aliases))


def first_text(payload: Any, aliases: Iterable[str]) -> str | None:
    value = deep_get_first(payload, aliases)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def first_date(payload: Any, aliases: Iterable[str]) -> date | None:
    return parse_date(deep_get_first(payload, aliases))


class SimpleRateLimiter:
    def __init__(self, min_interval_seconds: float) -> None:
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._lock = Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if now < self._next_allowed:
                time.sleep(self._next_allowed - now)
            self._next_allowed = time.monotonic() + self.min_interval_seconds
