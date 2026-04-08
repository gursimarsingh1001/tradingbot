from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from backend.db.models_investment import OfficialFinancialPeriod, OfficialInvestmentSnapshot


@dataclass(slots=True)
class PiotroskiScoreResult:
    symbol: str
    as_of_date: date
    f_score: int
    vote_yes: bool
    data_complete: bool
    missing_fields: list[str] = field(default_factory=list)
    signals_json: dict[str, Any] = field(default_factory=dict)


class PiotroskiScorer:
    YES_THRESHOLD = 7

    @staticmethod
    def _ratio(numerator: float | None, denominator: float | None) -> float | None:
        if numerator is None or denominator is None or denominator == 0:
            return None
        return float(numerator) / float(denominator)

    @classmethod
    def _roa(cls, period: OfficialFinancialPeriod | None) -> float | None:
        if period is None:
            return None
        if period.roa is not None:
            return period.roa
        return cls._ratio(period.net_profit, period.total_assets)

    @classmethod
    def _asset_turnover(cls, period: OfficialFinancialPeriod | None) -> float | None:
        if period is None:
            return None
        if period.asset_turnover is not None:
            return period.asset_turnover
        return cls._ratio(period.revenue, period.total_assets)

    @classmethod
    def score(
        cls,
        snapshot: OfficialInvestmentSnapshot,
        annual_periods: list[OfficialFinancialPeriod],
        as_of_date: date,
    ) -> PiotroskiScoreResult:
        sorted_periods = sorted(
            [period for period in annual_periods if period.period_end and period.period_end <= as_of_date],
            key=lambda period: period.period_end or date.min,
            reverse=True,
        )
        latest = sorted_periods[0] if len(sorted_periods) >= 1 else None
        prior = sorted_periods[1] if len(sorted_periods) >= 2 else None
        missing_fields: list[str] = []

        if latest is None:
            missing_fields.append("latest_annual_period")
        if prior is None:
            missing_fields.append("prior_annual_period")

        def evaluate(name: str, value: bool | None, fields: list[str]) -> tuple[int, dict[str, Any]]:
            for field_name in fields:
                if field_name not in missing_fields:
                    missing_fields.append(field_name)
            passed = bool(value) if value is not None else False
            return int(passed), {"pass": passed, "missing": value is None}

        latest_current_ratio = cls._ratio(
            latest.current_assets if latest is not None else None,
            latest.current_liabilities if latest is not None else None,
        )
        prior_current_ratio = cls._ratio(
            prior.current_assets if prior is not None else None,
            prior.current_liabilities if prior is not None else None,
        )
        latest_roa = cls._roa(latest)
        prior_roa = cls._roa(prior)
        latest_asset_turnover = cls._asset_turnover(latest)
        prior_asset_turnover = cls._asset_turnover(prior)

        signals_json: dict[str, Any] = {}
        f_score = 0

        score, payload = evaluate(
            "positive_roa",
            None if latest_roa is None else latest_roa > 0,
            [] if latest_roa is not None else ["latest_roa"],
        )
        f_score += score
        signals_json["positive_roa"] = payload

        score, payload = evaluate(
            "positive_operating_cash_flow",
            None if latest is None or latest.operating_cash_flow is None else latest.operating_cash_flow > 0,
            [] if latest is not None and latest.operating_cash_flow is not None else ["latest_operating_cash_flow"],
        )
        f_score += score
        signals_json["positive_operating_cash_flow"] = payload

        score, payload = evaluate(
            "roa_improved",
            None if latest_roa is None or prior_roa is None else latest_roa > prior_roa,
            [] if latest_roa is not None and prior_roa is not None else ["latest_roa", "prior_roa"],
        )
        f_score += score
        signals_json["roa_improved"] = payload

        score, payload = evaluate(
            "operating_cash_flow_gt_net_profit",
            None
            if latest is None or latest.operating_cash_flow is None or latest.net_profit is None
            else latest.operating_cash_flow > latest.net_profit,
            []
            if latest is not None and latest.operating_cash_flow is not None and latest.net_profit is not None
            else ["latest_operating_cash_flow", "latest_net_profit"],
        )
        f_score += score
        signals_json["operating_cash_flow_gt_net_profit"] = payload

        score, payload = evaluate(
            "lower_leverage",
            None
            if latest is None or prior is None or latest.total_debt is None or prior.total_debt is None
            else latest.total_debt < prior.total_debt,
            []
            if latest is not None and prior is not None and latest.total_debt is not None and prior.total_debt is not None
            else ["latest_total_debt", "prior_total_debt"],
        )
        f_score += score
        signals_json["lower_leverage"] = payload

        score, payload = evaluate(
            "higher_current_ratio",
            None if latest_current_ratio is None or prior_current_ratio is None else latest_current_ratio > prior_current_ratio,
            [] if latest_current_ratio is not None and prior_current_ratio is not None else ["latest_current_ratio", "prior_current_ratio"],
        )
        f_score += score
        signals_json["higher_current_ratio"] = payload

        score, payload = evaluate(
            "no_dilution",
            None
            if latest is None or prior is None or latest.shares_outstanding is None or prior.shares_outstanding is None
            else latest.shares_outstanding <= prior.shares_outstanding,
            []
            if latest is not None
            and prior is not None
            and latest.shares_outstanding is not None
            and prior.shares_outstanding is not None
            else ["latest_shares_outstanding", "prior_shares_outstanding"],
        )
        f_score += score
        signals_json["no_dilution"] = payload

        score, payload = evaluate(
            "higher_gross_margin",
            None
            if latest is None or prior is None or latest.gross_margin is None or prior.gross_margin is None
            else latest.gross_margin > prior.gross_margin,
            []
            if latest is not None and prior is not None and latest.gross_margin is not None and prior.gross_margin is not None
            else ["latest_gross_margin", "prior_gross_margin"],
        )
        f_score += score
        signals_json["higher_gross_margin"] = payload

        score, payload = evaluate(
            "higher_asset_turnover",
            None if latest_asset_turnover is None or prior_asset_turnover is None else latest_asset_turnover > prior_asset_turnover,
            [] if latest_asset_turnover is not None and prior_asset_turnover is not None else ["latest_asset_turnover", "prior_asset_turnover"],
        )
        f_score += score
        signals_json["higher_asset_turnover"] = payload

        signals_json["annual_periods_used"] = [
            {
                "period_end": period.period_end.isoformat() if period.period_end else None,
                "fiscal_label": period.fiscal_label,
            }
            for period in sorted_periods[:2]
        ]

        deduped_missing_fields = list(dict.fromkeys(missing_fields))
        return PiotroskiScoreResult(
            symbol=snapshot.symbol,
            as_of_date=as_of_date,
            f_score=f_score,
            vote_yes=f_score >= cls.YES_THRESHOLD,
            data_complete=not deduped_missing_fields,
            missing_fields=deduped_missing_fields,
            signals_json=signals_json,
        )


__all__ = ["PiotroskiScorer", "PiotroskiScoreResult"]
