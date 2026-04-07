from __future__ import annotations

from backend.engine.daily_report_service import DailyReportService


def main() -> None:
    result = DailyReportService().generate_daily_report(force=True)
    print(result)


if __name__ == "__main__":
    main()
