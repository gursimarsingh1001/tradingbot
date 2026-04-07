from __future__ import annotations

from backend.engine.alert_dispatcher import AlertDispatcher


def main() -> None:
    result = AlertDispatcher().dispatch_pending_notifications(catch_up=True)
    print(result)


if __name__ == "__main__":
    main()
