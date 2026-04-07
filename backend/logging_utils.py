from __future__ import annotations

import logging
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

from backend.config import get_settings


_configured = False


def _candidate_log_paths(settings) -> list[Path]:
    preferred_path = Path(settings.log_dir) / settings.log_file_name
    fallback_path = Path(tempfile.gettempdir()) / "trading-bot-logs" / settings.log_file_name
    candidates = [preferred_path]
    if fallback_path != preferred_path:
        candidates.append(fallback_path)
    return candidates


def configure_logging() -> None:
    global _configured
    if _configured:
        return

    settings = get_settings()

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    root_logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    configured_path: Path | None = None
    last_error: OSError | None = None
    for log_path in _candidate_log_paths(settings):
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=settings.log_max_bytes,
                backupCount=settings.log_backup_count,
                encoding="utf-8",
            )
        except OSError as exc:
            last_error = exc
            continue
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        configured_path = log_path
        break

    if configured_path is None:
        preferred_path = Path(settings.log_dir) / settings.log_file_name
        root_logger.warning("File logging disabled for %s: %s", preferred_path, last_error)
    elif configured_path.parent != Path(settings.log_dir):
        root_logger.info(
            "Preferred log path %s was not writable; using fallback file logging at %s",
            Path(settings.log_dir),
            configured_path,
        )

    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
