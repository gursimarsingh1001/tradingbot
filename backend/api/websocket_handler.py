from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import and_, or_, select

from backend.config import get_settings
from backend.api.routes_paper_trades import serialize_trade
from backend.db.postgres import Notification, PaperTrade, get_config_value, session_scope
from backend.db.redis_client import get_cache
from backend.engine.market_data_service import get_market_data_service


settings = get_settings()
router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.discard(websocket)

    async def send_snapshot(self, websocket: WebSocket, *, last_notification_id: str | None = None) -> None:
        cache = get_cache()
        market_data = get_market_data_service().refresh_market_cache(force=False)
        indices = market_data.get(
            "indices",
            {
                "NIFTY50": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
                "BANKNIFTY": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
                "SENSEX": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
                "INDIA_VIX": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
            },
        )
        watchlist_prices = market_data.get("watchlist_prices", [])
        with session_scope() as session:
            notifications_query = select(Notification).order_by(Notification.created_at.desc()).limit(20)
            if last_notification_id:
                anchor = session.get(Notification, last_notification_id)
                if anchor is not None:
                    notifications_query = (
                        select(Notification)
                        .where(Notification.created_at > anchor.created_at)
                        .order_by(Notification.created_at.asc())
                    )
            notifications = session.scalars(notifications_query).all()
            paper_trades = session.scalars(
                select(PaperTrade)
                .where(
                    or_(
                        PaperTrade.entry_date == date.today(),
                        and_(
                            PaperTrade.signal_type == "INVESTMENT",
                            PaperTrade.exit_date.is_(None),
                        ),
                    )
                )
                .order_by(PaperTrade.created_at.desc())
            ).all()
            kill_switch = get_config_value(session, "kill_switch", {"active": False, "reason": None})
        active_signals = cache.get_json("live:active_signals", [])

        payload = {
            "timestamp": datetime.now(tz=settings.tzinfo).isoformat(),
            "indices": indices
            or {
                "NIFTY50": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
                "BANKNIFTY": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
                "SENSEX": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
                "INDIA_VIX": {"value": 0.0, "change": 0.0, "change_pct": 0.0},
            },
            "watchlist_prices": watchlist_prices or [],
            "signals": active_signals
            or [
                {
                    "trade_id": trade.trade_id,
                    "stock_symbol": trade.stock_symbol,
                    "strategy_name": trade.strategy_name,
                    "confidence_score": trade.confidence_score,
                    "entry_price": trade.entry_price,
                    "entry_zone_low": trade.entry_zone_low,
                    "entry_zone_high": trade.entry_zone_high,
                    "signal_type": trade.signal_type,
                    "status": "OPEN" if trade.exit_date is None else "CLOSED",
                }
                for trade in paper_trades
            ],
            "paper_trades": [serialize_trade(trade).model_dump(mode="json", by_alias=True) for trade in paper_trades],
            "notifications": [
                {
                    "id": notification.id,
                    "type": notification.type,
                    "title": notification.title,
                    "body": notification.body,
                    "color": notification.color,
                    "is_read": notification.is_read,
                    "related_stock": notification.related_stock,
                    "created_at": notification.created_at.isoformat() if notification.created_at else None,
                }
                for notification in notifications
            ],
            "kill_switch": kill_switch,
        }
        await websocket.send_json(payload)


manager = ConnectionManager()


@router.websocket("/ws/live")
async def websocket_live(websocket: WebSocket) -> None:
    last_notification_id = websocket.query_params.get("lastNotificationId")
    await manager.connect(websocket)
    try:
        while True:
            await manager.send_snapshot(websocket, last_notification_id=last_notification_id)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        manager.disconnect(websocket)
