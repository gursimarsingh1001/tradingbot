from __future__ import annotations

from datetime import datetime
from typing import Any

import requests
from sqlalchemy import select

from backend.config import get_settings
from backend.db.postgres import BotConfig, Notification, session_scope


settings = get_settings()


class AlertDispatcher:
    ALERT_STATE_KEY = "alert_dispatch_state"
    DEFAULT_ALLOWED_TYPES = {
        "PAPER_TRADE_OPENED",
        "PAPER_TRADE_CLOSED",
        "TARGET",
        "KILL_SWITCH",
        "INVESTMENT_SCAN",
        "MARKET_HOLIDAY",
        "AFTER_MARKET",
        "BACKUP",
        "DAILY_REPORT",
        "LEARNING",
    }

    def __init__(self) -> None:
        self.bot_token = settings.telegram_bot_token.strip()
        self.chat_id = settings.telegram_chat_id.strip()
        self.enabled = bool(settings.alerts_enabled and self.bot_token and self.chat_id)
        self.session = requests.Session()
        self.timeout = 12

    def _state(self) -> dict[str, Any]:
        with session_scope() as session:
            record = session.get(BotConfig, self.ALERT_STATE_KEY)
            if record is None:
                return {
                    "lastSentAt": None,
                    "lastNotificationId": None,
                    "lastDeliveredCount": 0,
                    "lastError": None,
                }
            return dict(record.value or {})

    def _store_state(self, payload: dict[str, Any]) -> None:
        with session_scope() as session:
            record = session.get(BotConfig, self.ALERT_STATE_KEY)
            if record is None:
                session.add(BotConfig(key=self.ALERT_STATE_KEY, value=payload))
            else:
                record.value = payload

    def _format_notification(self, notification: Notification) -> str:
        stock_prefix = f"[{notification.related_stock}] " if notification.related_stock else ""
        return (
            f"Trading Bot Alert\n"
            f"{stock_prefix}{notification.title or notification.type or 'Update'}\n\n"
            f"{notification.body or ''}\n\n"
            f"Time: {(notification.created_at.isoformat() if notification.created_at else datetime.now(tz=settings.tzinfo).isoformat())}"
        )

    def send_text(self, title: str, body: str) -> bool:
        if not self.enabled:
            return False
        response = self.session.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": f"{title}\n\n{body}",
                "disable_web_page_preview": True,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return True

    def dispatch_pending_notifications(self, *, limit: int = 25, catch_up: bool = False) -> dict[str, Any]:
        now = datetime.now(tz=settings.tzinfo)
        state = self._state()
        if not self.enabled:
            state.update(
                {
                    "lastError": "Telegram alerts disabled or credentials missing.",
                    "lastDeliveredCount": 0,
                }
            )
            self._store_state(state)
            return {"enabled": False, "sent": 0, "skipped": 0}

        last_sent_at = state.get("lastSentAt")
        if last_sent_at is None and not catch_up:
            baseline = {
                "lastSentAt": now.isoformat(),
                "lastNotificationId": None,
                "lastDeliveredCount": 0,
                "lastError": None,
            }
            self._store_state(baseline)
            return {"enabled": True, "sent": 0, "skipped": 0, "baseline": True}

        with session_scope() as session:
            query = select(Notification).order_by(Notification.created_at.asc())
            if last_sent_at:
                anchor = datetime.fromisoformat(str(last_sent_at))
                query = query.where(Notification.created_at > anchor)
            notifications = [
                notification
                for notification in session.scalars(query.limit(limit)).all()
                if (notification.type or "") in self.DEFAULT_ALLOWED_TYPES
            ]

        sent = 0
        last_notification_id = state.get("lastNotificationId")
        try:
            for notification in notifications:
                self.send_text(notification.title or "Trading Bot Alert", self._format_notification(notification))
                sent += 1
                last_notification_id = notification.id
            state.update(
                {
                    "lastSentAt": notifications[-1].created_at.isoformat() if notifications else now.isoformat(),
                    "lastNotificationId": last_notification_id,
                    "lastDeliveredCount": sent,
                    "lastError": None,
                }
            )
        except Exception as exc:
            state.update(
                {
                    "lastDeliveredCount": sent,
                    "lastError": f"{type(exc).__name__}: {exc}",
                }
            )
            self._store_state(state)
            raise

        self._store_state(state)
        return {"enabled": True, "sent": sent, "skipped": 0}
