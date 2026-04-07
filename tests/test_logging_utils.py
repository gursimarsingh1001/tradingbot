import logging
from pathlib import Path

import backend.logging_utils as logging_utils


def test_configure_logging_falls_back_to_console_when_file_handler_fails(monkeypatch):
    logging_utils._configured = False

    def raise_permission_error(*args, **kwargs):
        raise PermissionError("no file access")

    monkeypatch.setattr(logging_utils, "RotatingFileHandler", raise_permission_error)

    logging_utils.configure_logging()

    root_logger = logging.getLogger()
    assert root_logger.handlers
    assert all(not hasattr(handler, "baseFilename") for handler in root_logger.handlers)

    logging_utils._configured = False


def test_configure_logging_uses_temp_fallback_when_preferred_path_fails(monkeypatch, tmp_path):
    logging_utils._configured = False

    class StubSettings:
        log_dir = tmp_path / "non_writable"
        log_level = "INFO"
        log_file_name = "trading-bot.log"
        log_max_bytes = 1000
        log_backup_count = 1

    real_handler = logging_utils.RotatingFileHandler
    fallback_dir = tmp_path / "fallback-logs"

    def fake_candidate_paths(_settings):
        return [
            Path(StubSettings.log_dir) / StubSettings.log_file_name,
            fallback_dir / StubSettings.log_file_name,
        ]

    def maybe_fail(path, *args, **kwargs):
        if Path(path) == Path(StubSettings.log_dir) / StubSettings.log_file_name:
            raise PermissionError("preferred path blocked")
        return real_handler(path, *args, **kwargs)

    monkeypatch.setattr(logging_utils, "get_settings", lambda: StubSettings())
    monkeypatch.setattr(logging_utils, "_candidate_log_paths", fake_candidate_paths)
    monkeypatch.setattr(logging_utils, "RotatingFileHandler", maybe_fail)

    logging_utils.configure_logging()

    root_logger = logging.getLogger()
    file_handlers = [handler for handler in root_logger.handlers if hasattr(handler, "baseFilename")]
    assert file_handlers
    assert Path(file_handlers[0].baseFilename).parent == fallback_dir

    logging_utils._configured = False
