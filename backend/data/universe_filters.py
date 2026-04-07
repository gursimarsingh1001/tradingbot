from __future__ import annotations

from typing import Iterable


PURE_EQUITY_SERIES = {"EQ"}

# Broad passive-product / ETF / structured-product fragments that should never
# enter the trading universe when the bot is meant to work on plain stocks only.
EXCLUDED_INSTRUMENT_FRAGMENTS = (
    "BEES",
    "ETF",
    "ETF ",
    " ETF",
    "LIQUID",
    "NIFTY",
    "SENSEX",
    "NEXT50",
    "MIDCAP",
    "SMALLCAP",
    "MICROCAP",
    "MOMENTUM",
    "LOWVOL",
    "QUALITY",
    "DIVOPP",
    "COMMOI",
    "COMMO",
    "GSEC",
    "BOND",
    "INDEX",
    "GOLD",
    "SILVER",
    "PSUBNK",
    "PSUBANK",
    "BANKBEES",
    "GOLDBEES",
    "SILVERBEES",
    "MONETF",
    "FETF",
    "HETF",
)

PRODUCT_LIKE_FRAGMENTS = (
    "BANK",
    "GOLD",
    "SILVER",
    "BOND",
    "FUND",
    "ETF",
    "INDEX",
    "VALUE",
    "ALPHA",
    "MOMENTUM",
    "QUALITY",
    "LOWVOL",
)


def _join_parts(parts: Iterable[str | None]) -> str:
    return " ".join(part.strip().upper() for part in parts if part and part.strip())


def build_security_haystack(
    *,
    symbol: str | None,
    company_name: str | None,
    trading_symbol: str | None,
) -> str:
    return _join_parts((symbol, company_name, trading_symbol))


def is_pure_nse_stock(
    *,
    symbol: str | None,
    company_name: str | None,
    trading_symbol: str | None,
    exchange: str | None,
    series: str | None,
    instrument_type: str | None = None,
) -> bool:
    if (exchange or "").upper() != "NSE":
        return False

    if (instrument_type or "").strip():
        return False

    if (series or "").upper() not in PURE_EQUITY_SERIES:
        return False

    haystack = build_security_haystack(
        symbol=symbol,
        company_name=company_name,
        trading_symbol=trading_symbol,
    )
    if not haystack:
        return False

    if any(fragment in haystack for fragment in EXCLUDED_INSTRUMENT_FRAGMENTS):
        return False

    symbol_text = (symbol or "").strip().upper()
    name_text = (company_name or "").strip().upper()
    placeholder_name = bool(symbol_text) and symbol_text == name_text
    if placeholder_name and any(fragment in haystack for fragment in PRODUCT_LIKE_FRAGMENTS):
        return False

    return True
