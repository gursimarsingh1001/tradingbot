from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import to_camel
from backend.db.postgres import MistakeLog, PaperTrade, ScoringWeight, get_config_value, get_db


router = APIRouter(prefix="/api/learning", tags=["learning"])


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class WeightSnapshot(CamelModel):
    pattern_weight: float
    ma_weight: float
    volume_weight: float
    news_weight: float
    regime_weight: float
    fundamental_weight: float
    model_accuracy: float | None


class MistakeItem(CamelModel):
    id: int
    trade_id: str | None
    stock_symbol: str | None
    strategy_name: str | None
    conditions_at_loss: dict | None
    adjustment_made: str | None
    created_at: str | None


class LearningResponse(CamelModel):
    current_weights: WeightSnapshot | None
    initial_weights: WeightSnapshot | None
    model_accuracy: float
    mistakes: list[MistakeItem]


@router.get("/mistakes", response_model=LearningResponse)
def get_learning_log(db: Session = Depends(get_db)) -> LearningResponse:
    weights = db.scalars(select(ScoringWeight).order_by(ScoringWeight.created_at.desc())).all()
    latest = weights[0] if weights else None
    initial = weights[-1] if weights else None
    mistakes = db.execute(
        select(MistakeLog, PaperTrade)
        .join(PaperTrade, PaperTrade.trade_id == MistakeLog.trade_id)
        .order_by(MistakeLog.created_at.desc())
        .limit(100)
    ).all()
    items = [
        MistakeItem(
            id=mistake.id,
            trade_id=mistake.trade_id,
            stock_symbol=trade.stock_symbol,
            strategy_name=trade.strategy_name,
            conditions_at_loss=mistake.conditions_at_loss,
            adjustment_made=mistake.adjustment_made,
            created_at=mistake.created_at.isoformat() if mistake.created_at else None,
        )
        for mistake, trade in mistakes
    ]
    accuracy = float(get_config_value(db, "current_model_accuracy", {"value": 0.0}).get("value", 0.0))
    return LearningResponse(
        current_weights=WeightSnapshot.model_validate(latest) if latest else None,
        initial_weights=WeightSnapshot.model_validate(initial) if initial else None,
        model_accuracy=accuracy,
        mistakes=items,
    )
