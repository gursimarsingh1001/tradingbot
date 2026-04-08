from __future__ import annotations

from sqlalchemy import text

from backend.db.postgres import engine
from backend.logging_utils import get_logger
from backend.scheduler import TradingSchedulerService


logger = get_logger(__name__)

_embedded_service: TradingSchedulerService | None = None
_scheduler_lock_connection = None


def _acquire_scheduler_lock() -> bool:
    global _scheduler_lock_connection
    if _scheduler_lock_connection is not None:
        return True
    connection = engine.connect()
    acquired = bool(connection.execute(text("SELECT pg_try_advisory_lock(20260331, 9151530)")).scalar())
    if acquired:
        _scheduler_lock_connection = connection
        return True
    connection.close()
    logger.info("another process already owns the embedded scheduler lock")
    return False


def _release_scheduler_lock() -> None:
    global _scheduler_lock_connection
    if _scheduler_lock_connection is None:
        return
    try:
        _scheduler_lock_connection.execute(text("SELECT pg_advisory_unlock(20260331, 9151530)"))
    finally:
        _scheduler_lock_connection.close()
        _scheduler_lock_connection = None


def start_embedded_schedulers() -> TradingSchedulerService:
    global _embedded_service
    if _embedded_service is None:
        _embedded_service = TradingSchedulerService()
        _embedded_service.configure()

    if not _acquire_scheduler_lock():
        return _embedded_service

    _embedded_service.start()
    _embedded_service.catch_up_live_session_if_needed()
    _embedded_service.catch_up_after_market_if_needed()
    return _embedded_service


def get_embedded_scheduler_service() -> TradingSchedulerService | None:
    return _embedded_service


def stop_embedded_schedulers() -> None:
    global _embedded_service
    if _embedded_service is None:
        _release_scheduler_lock()
        return
    _embedded_service.shutdown()
    _release_scheduler_lock()
