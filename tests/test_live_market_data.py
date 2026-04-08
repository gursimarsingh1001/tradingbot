from __future__ import annotations

from backend.data.dhan_client import DhanClient
from backend.data.global_market_client import GlobalMarketClient


def test_dhan_symbol_lookup_supports_trading_symbol_and_company_alias() -> None:
    client = DhanClient()
    client._map_loaded = True
    client._security_id_map = {
        "RELIANCE": {
            "token": "2885",
            "exchange": "NSE",
            "exchange_segment": "NSE_EQ",
            "instrument_type": "EQUITY",
            "is_index": False,
        },
        "RELIANCEINDUSTRIESLTD": {
            "token": "2885",
            "exchange": "NSE",
            "exchange_segment": "NSE_EQ",
            "instrument_type": "EQUITY",
            "is_index": False,
        },
    }

    assert client.get_dhan_info("RELIANCE")["token"] == "2885"
    assert client.get_dhan_info("Reliance Industries Ltd")["token"] == "2885"


def test_public_quote_page_parser_extracts_value_change_and_timestamp() -> None:
    client = GlobalMarketClient(session=None)
    html = """
    <html><body>
      <h1>Gift Nifty</h1>
      <h2>GIFTNIFTY Share Price</h2>
      <div>NSE</div>
      <div>23,803.50</div>
      <div>758.50 (3.29%)</div>
      <div>Last Updated on 08 Apr 2026 at 08:16</div>
    </body></html>
    """

    parsed = client._parse_public_quote_page(html)

    assert parsed["value"] == 23803.5
    assert parsed["change"] == 758.5
    assert parsed["change_pct"] == 0.0329
    assert parsed["source"] == "DHAN_PUBLIC_PAGE"
    assert parsed["is_delayed"] is True
    assert parsed["updated_at"].startswith("2026-04-08T08:16")
