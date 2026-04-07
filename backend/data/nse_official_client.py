from __future__ import annotations

from backend.data.nse_client import NSEClient, get_nse_client


NSEOfficialClient = NSEClient


def get_nse_official_client() -> NSEClient:
    return get_nse_client()


__all__ = ["NSEOfficialClient", "get_nse_official_client"]
