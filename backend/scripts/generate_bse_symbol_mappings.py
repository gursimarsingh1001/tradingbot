from __future__ import annotations

import json
from pathlib import Path

from backend.data.bse_mapping_builder import build_bse_symbol_mappings, load_nifty500_isin_map, load_openapi_bse_rows


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    symbols_path = repo_root / "backend" / "config" / "nifty500_symbols.json"
    mappings_path = repo_root / "backend" / "config" / "bse_symbol_mappings.json"
    master_path = repo_root / "tmp" / "OpenAPIScripMaster.json"
    nifty_csv_path = repo_root / "tmp" / "ind_nifty500list.csv"

    symbols_payload = json.loads(symbols_path.read_text(encoding="utf-8"))
    if not isinstance(symbols_payload, list):
        raise RuntimeError(f"Unexpected symbol config payload in {symbols_path}")

    mappings, unresolved = build_bse_symbol_mappings(
        symbols_payload,
        load_openapi_bse_rows(master_path),
        load_nifty500_isin_map(nifty_csv_path),
    )
    mappings_path.write_text(json.dumps(mappings, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "mappings_written": len(mappings),
                "unresolved_count": len(unresolved),
                "unresolved_symbols": unresolved,
                "output": str(mappings_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
