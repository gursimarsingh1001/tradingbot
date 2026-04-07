from datetime import datetime
from zoneinfo import ZoneInfo

from backend.config import Settings


def test_market_holidays_path_falls_back_to_latest_available_file(tmp_path):
    current_year = datetime.now(tz=ZoneInfo("Asia/Kolkata")).year
    older = tmp_path / f"nse_trading_holidays_{current_year - 1}.json"
    newer = tmp_path / f"nse_trading_holidays_{current_year + 1}.json"
    older.write_text("[]", encoding="utf-8")
    newer.write_text("[]", encoding="utf-8")

    settings = Settings(MARKET_HOLIDAYS_DIR=str(tmp_path))

    assert settings.market_holidays_path == newer
