from __future__ import annotations

from backend.engine.backup_service import BackupService


def main() -> None:
    result = BackupService().create_daily_backup(force=True)
    print(
        {
            "created": result.created,
            "backup_dir": result.backup_dir,
            "tables": result.tables,
            "retained_days": result.retained_days,
            "skipped": result.skipped,
        }
    )


if __name__ == "__main__":
    main()
