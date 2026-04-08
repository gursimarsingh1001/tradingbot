from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass(slots=True)
class FieldReconciliation:
    field_name: str
    value: Any
    selected_source: str | None
    confidence: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    cross_checks: list[dict[str, Any]] = field(default_factory=list)
    mismatch: bool = False
    stale: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "value": self.value.isoformat() if isinstance(self.value, date) else self.value,
            "selected_source": self.selected_source,
            "confidence": self.confidence,
            "stale": self.stale,
            "mismatch": self.mismatch,
            "candidates": self.candidates,
            "cross_checks": self.cross_checks,
        }


@dataclass(slots=True)
class ReconciledResult:
    values: dict[str, Any]
    fields: dict[str, FieldReconciliation]
    fill_rate: float
    mismatches: list[str]

    def to_data_sources_payload(self) -> dict[str, Any]:
        return {
            "reconciled_at": datetime.utcnow().isoformat(),
            "fill_rate": self.fill_rate,
            "mismatches": self.mismatches,
            "fields": {field_name: result.to_payload() for field_name, result in self.fields.items()},
        }


class DataReconciler:
    def __init__(self, *, tolerance: float = 0.10) -> None:
        self.tolerance = max(0.0, float(tolerance))

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return value

    def _values_match(self, left: Any, right: Any) -> bool:
        if left is None or right is None:
            return False
        if isinstance(left, (datetime, date)) and isinstance(right, (datetime, date)):
            return left == right
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            left_value = float(left)
            right_value = float(right)
            baseline = max(abs(left_value), abs(right_value), 1.0)
            return abs(left_value - right_value) <= baseline * self.tolerance
        return left == right

    def reconcile_field(self, field_name: str, candidates: list[dict[str, Any]]) -> FieldReconciliation:
        usable = [candidate for candidate in candidates if candidate.get("value") is not None]
        serialized_candidates = [
            {
                "source": candidate.get("source"),
                "value": self._serialize_value(candidate.get("value")),
                "stale": bool(candidate.get("stale")),
            }
            for candidate in candidates
        ]
        if not usable:
            return FieldReconciliation(
                field_name=field_name,
                value=None,
                selected_source=None,
                confidence="NONE",
                candidates=serialized_candidates,
            )

        selected = usable[0]
        mismatch = False
        cross_checks: list[dict[str, Any]] = []
        for candidate in usable[1:]:
            matches = self._values_match(selected.get("value"), candidate.get("value"))
            if not matches:
                mismatch = True
            cross_checks.append(
                {
                    "source": candidate.get("source"),
                    "value": self._serialize_value(candidate.get("value")),
                    "matches_selected": matches,
                    "stale": bool(candidate.get("stale")),
                }
            )
        if len(usable) == 1:
            confidence = "SINGLE"
        elif not mismatch:
            confidence = "HIGH"
        else:
            confidence = "MEDIUM"
        return FieldReconciliation(
            field_name=field_name,
            value=selected.get("value"),
            selected_source=selected.get("source"),
            confidence=confidence,
            candidates=serialized_candidates,
            cross_checks=cross_checks,
            mismatch=mismatch,
            stale=bool(selected.get("stale")),
        )

    def reconcile_fields(self, field_candidates: dict[str, list[dict[str, Any]]]) -> ReconciledResult:
        fields: dict[str, FieldReconciliation] = {}
        values: dict[str, Any] = {}
        mismatches: list[str] = []
        for field_name, candidates in field_candidates.items():
            result = self.reconcile_field(field_name, candidates)
            fields[field_name] = result
            values[field_name] = result.value
            if result.mismatch:
                mismatches.append(field_name)
        total_fields = len(field_candidates)
        populated_fields = sum(1 for value in values.values() if value is not None)
        fill_rate = 0.0 if total_fields == 0 else round(populated_fields / total_fields, 4)
        return ReconciledResult(
            values=values,
            fields=fields,
            fill_rate=fill_rate,
            mismatches=mismatches,
        )


__all__ = ["DataReconciler", "FieldReconciliation", "ReconciledResult"]
