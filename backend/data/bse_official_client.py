from __future__ import annotations

from backend.data.bse_client import BSEClient, get_bse_client


BSEOfficialClient = BSEClient


def get_bse_official_client() -> BSEClient:
    return get_bse_client()


__all__ = ["BSEOfficialClient", "get_bse_official_client"]
