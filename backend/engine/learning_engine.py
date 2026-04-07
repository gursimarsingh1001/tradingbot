from __future__ import annotations

from pathlib import Path

import joblib
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sqlalchemy import select

from backend.config import get_settings
from backend.db.postgres import PaperTrade, ScoringWeight, get_config_value, session_scope, upsert_config_value


settings = get_settings()


class LearningEngine:
    def weekly_retrain(self) -> dict[str, float | bool]:
        with session_scope() as session:
            rows = session.scalars(
                select(PaperTrade).where(PaperTrade.exit_date.is_not(None)).order_by(PaperTrade.created_at.asc())
            ).all()

        training_rows: list[dict[str, float | int | str]] = []
        for row in rows:
            metadata = row.metadata_json or {}
            if metadata.get("plan_only"):
                continue
            if str(row.exit_reason or "").upper() == "PLAN_EXPIRED":
                continue
            if row.entry_date is None or row.entry_time is None or row.entry_price is None or not row.shares:
                continue
            training_rows.append(
                {
                    "confidence_score": row.confidence_score or 0.0,
                    "regime_encoded": row.regime_at_entry or "UNKNOWN",
                    "news_score": row.news_score_at_entry or 0.0,
                    "volume_ratio": metadata.get("volume_ratio", 0.0),
                    "rsi_at_entry": metadata.get("rsi_at_entry", 0.0),
                    "adx_at_entry": metadata.get("adx_at_entry", 0.0),
                    "pattern_encoded": row.pattern_name or "UNKNOWN",
                    "strategy_encoded": row.strategy_name or "UNKNOWN",
                    "direction_encoded": str(metadata.get("direction") or "UNKNOWN"),
                    "product_type_encoded": str(metadata.get("product_type") or "UNKNOWN"),
                    "source_kind_encoded": str(metadata.get("opened_from") or "UNKNOWN"),
                    "hour_of_day": int(row.entry_time.hour) if row.entry_time else 0,
                    "day_of_week": int(row.entry_date.weekday()) if row.entry_date else 0,
                    "days_to_earnings": metadata.get("days_to_earnings", 999),
                    "sector_encoded": metadata.get("sector", "UNKNOWN"),
                    "was_profitable": int(bool(row.was_profitable)),
                }
            )

        df = pd.DataFrame(training_rows)
        if len(df) < 10 or df["was_profitable"].nunique() < 2:
            return {"updated": False, "accuracy": 0.0}

        categorical = [
            "regime_encoded",
            "pattern_encoded",
            "strategy_encoded",
            "sector_encoded",
            "direction_encoded",
            "product_type_encoded",
            "source_kind_encoded",
        ]
        X = pd.get_dummies(df.drop(columns=["was_profitable"]), columns=categorical)
        y = df["was_profitable"].astype(int)

        split = int(len(X) * 0.8)
        if split <= 0 or split >= len(X):
            return {"updated": False, "accuracy": 0.0}
        X_train, X_val = X.iloc[:split], X.iloc[split:]
        y_train, y_val = y.iloc[:split], y.iloc[split:]
        if len(X_val) == 0 or y_train.nunique() < 2 or y_val.nunique() < 1:
            return {"updated": False, "accuracy": 0.0}

        model = GradientBoostingClassifier(n_estimators=100, max_depth=4)
        model.fit(X_train, y_train)

        val_accuracy = float(model.score(X_val, y_val))
        with session_scope() as session:
            current_accuracy = float(get_config_value(session, "current_model_accuracy", {"value": 0.0}).get("value", 0.0))
            if val_accuracy > current_accuracy:
                Path(settings.scoring_model_path).parent.mkdir(parents=True, exist_ok=True)
                joblib.dump({"model": model, "columns": list(X.columns)}, settings.scoring_model_path)
                self.update_weights_from_feature_importance(model, X.columns.tolist())
                upsert_config_value(session, "current_model_accuracy", {"value": val_accuracy})
                return {"updated": True, "accuracy": val_accuracy}
        return {"updated": False, "accuracy": val_accuracy}

    def update_weights_from_feature_importance(self, model: GradientBoostingClassifier, features: list[str]) -> None:
        raw_importance = dict(zip(features, model.feature_importances_))
        grouped = {
            "pattern_weight": 0.0,
            "ma_weight": 0.0,
            "volume_weight": 0.0,
            "news_weight": 0.0,
            "regime_weight": 0.0,
            "fundamental_weight": 0.0,
        }
        for feature, value in raw_importance.items():
            if feature.startswith("pattern_") or feature == "pattern_encoded":
                grouped["pattern_weight"] += value
            elif feature in {"rsi_at_entry", "adx_at_entry"}:
                grouped["ma_weight"] += value
            elif feature == "volume_ratio":
                grouped["volume_weight"] += value
            elif feature == "news_score":
                grouped["news_weight"] += value
            elif feature.startswith("regime_"):
                grouped["regime_weight"] += value
            else:
                grouped["fundamental_weight"] += value

        total = sum(grouped.values()) or 1.0
        normalized = {key: value / total for key, value in grouped.items()}
        with session_scope() as session:
            session.add(
                ScoringWeight(
                    pattern_weight=normalized["pattern_weight"],
                    ma_weight=normalized["ma_weight"],
                    volume_weight=normalized["volume_weight"],
                    news_weight=normalized["news_weight"],
                    regime_weight=normalized["regime_weight"],
                    fundamental_weight=normalized["fundamental_weight"],
                    model_accuracy=float(get_config_value(session, "current_model_accuracy", {"value": 0.0}).get("value", 0.0)),
                )
            )
