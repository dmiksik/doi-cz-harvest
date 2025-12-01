#!/usr/bin/env python3
"""
harvest_cz_datasets.py

Sklidí DOI z DataCite a Crossref, které mají afiliaci na české organizace
(podle ROR dumpu) a jsou typu dataset. Výsledkem jsou dva JSONL soubory:
- datacite_cz_datasets.jsonl
- crossref_cz_datasets.jsonl
"""

import argparse
import json
import os
import sys
import time
from typing import Dict, Any, List, Optional

import requests

DATACITE_API = "https://api.datacite.org/dois"
CROSSREF_API = "https://api.crossref.org/works"

DATACITE_PAGE_SIZE = 1000
CROSSREF_ROWS = 1000
REQUEST_DELAY = 0.5  # seconds – buď hodný k API


def is_cz_org(record: Dict[str, Any], country_code: str = "CZ") -> bool:
    """Vrátí True, pokud má organizace v ROR dumpu country_code == 'CZ'."""
    # Schema v2.x – locations[*].geonames_details.country_code
    locations = record.get("locations") or []
    if isinstance(locations, list):
        for loc in locations:
            details = loc.get("geonames_details") or {}
            if details.get("country_code") == country_code:
                return True

    # Fallback pro schema v1 – addresses[*].country_code
    addresses = record.get("addresses") or []
    if isinstance(addresses, list):
        for addr in addresses:
            if addr.get("country_code") == country_code:
                return True

    return False


def load_cz_ror_ids(ror_path: str, country_code: str = "CZ") -> List[str]:
    """Načte ROR dump (JSON) a vrátí seznam ROR ID pro danou zemi."""
    with open(ror_path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"Chyba při čtení {ror_path}: {e}", file=sys.stderr)
            raise

    cz_ids: List[str] = []
    for rec in data:
        if is_cz_org(rec, country_code=country_code):
            ror_id = rec.get("id")
            if ror_id:
                cz_ids.append(ror_id)

    return cz_ids


def harvest_datacite_for_ror(
    ror_id: str,
    out_fh,
    mailto: Optional[str] = None,
) -> int:
    """Sklidí všechny DataCite DOI typu Dataset pro daný ROR ID.

    Záznamy zapisuje do out_fh jako JSONL.
    Vrací počet zapsaných záznamů.
    """
    session = requests.Session()
    total = 0

    # první dotaz s klasickými parametry
    params = {
        "affiliation-id": ror_id,
        "resource-type-id": "Dataset",  # resourceTypeGeneral
        "affiliation": "true",
        "page[cursor]": 1,
        "page[size]": DATACITE_PAGE_SIZE,
        "detail": "true",
    }
    if mailto:
        # DataCite ofiko nemá mailto, ale dáme aspoň do User-Agent
        session.headers["User-Agent"] = f"cz-ror-harvester (mailto:{mailto})"
    url = DATACITE_API
    next_url: Optional[str] = None

    while True:
        if next_url:
            # další stránky bereme z links.next – obsahuje celý URL
            resp = session.get(next_url, timeout=60)
        else:
            resp = session.get(url, params=params, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data", [])
        if not data:
            break

        for item in data:
            doi = item.get("id")
            record = {
                "source": "datacite",
                "ror_id": ror_id,
                "doi": doi,
                "record": item,
            }
            out_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            total += 1

        links = payload.get("links", {})
        next_url = links.get("next")
        if not next_url:
            break

        time.sleep(REQUEST_DELAY)

    return total


def harvest_crossref_for_ror(
    ror_id: str,
    out_fh,
    mailto: Optional[str] = None,
) -> int:
    """Sklidí všechny Crossref DOI typu dataset pro daný ROR ID.

    Záznamy zapisuje do out_fh jako JSONL.
    Vrací počet zapsaných záznamů.
    """
    session = requests.Session()
    if mailto:
        session.headers["User-Agent"] = f"cz-ror-harvester (mailto:{mailto})"

    cursor = "*"
    total = 0

    while True:
        params = {
            "filter": f"type:dataset,ror-id:{ror_id}",
            "rows": CROSSREF_ROWS,
            "cursor": cursor,
        }
        if mailto:
            params["mailto"] = mailto

        resp = session.get(CROSSREF_API, params=params, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        message = payload.get("message", {})
        items = message.get("items", [])
        if not items:
            break

        for item in items:
            doi = item.get("DOI")
            record = {
                "source": "crossref",
                "ror_id": ror_id,
                "doi": doi,
                "record": item,
            }
            out_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            total += 1

        next_cursor = message.get("next-cursor")
        if not next_cursor:
            break
        cursor = next_cursor

        # konec je, když je poslední batch kratší než rows – to pokryje i prázdný items výše
        if len(items) < CROSSREF_ROWS:
            break

        time.sleep(REQUEST_DELAY)

    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Sklidí DOI typu dataset z DataCite a Crossref pro CZ organizace "
            "podle ROR dumpu."
        )
    )
    parser.add_argument(
        "--ror-dump",
        required=True,
        help="Cesta k ROR JSON dumpu (ideálně *_schema_v2.json)",
    )
    parser.add_argument(
        "--out-dir",
        default="out",
        help="Výstupní adresář (default: ./out)",
    )
    parser.add_argument(
        "--mailto",
        default=None,
        help="E-mail pro identifikaci vůči Crossref API (doporučeno)",
    )
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    datacite_path = os.path.join(args.out_dir, "datacite_cz_datasets.jsonl")
    crossref_path = os.path.join(args.out_dir, "crossref_cz_datasets.jsonl")

    print(f"Načítám ROR dump z {args.ror_dump} ...", file=sys.stderr)
    cz_ror_ids = load_cz_ror_ids(args.ror_dump, country_code="CZ")
    print(f"Nalezeno {len(cz_ror_ids)} CZ organizací v ROR.", file=sys.stderr)

    # DataCite
    with open(datacite_path, "w", encoding="utf-8") as f_dc:
        total_dc = 0
        for i, ror_id in enumerate(cz_ror_ids, start=1):
            print(
                f"[DataCite] ({i}/{len(cz_ror_ids)}) {ror_id} ...",
                file=sys.stderr,
            )
            try:
                count = harvest_datacite_for_ror(ror_id, f_dc, mailto=args.mailto)
                total_dc += count
            except Exception as e:
                print(f"  Chyba pro {ror_id} (DataCite): {e}", file=sys.stderr)
            time.sleep(REQUEST_DELAY)
    print(f"Hotovo DataCite, celkem {total_dc} záznamů.", file=sys.stderr)

    # Crossref
    with open(crossref_path, "w", encoding="utf-8") as f_cr:
        total_cr = 0
        for i, ror_id in enumerate(cz_ror_ids, start=1):
            print(
                f"[Crossref] ({i}/{len(cz_ror_ids)}) {ror_id} ...",
                file=sys.stderr,
            )
            try:
                count = harvest_crossref_for_ror(ror_id, f_cr, mailto=args.mailto)
                total_cr += count
            except Exception as e:
                print(f"  Chyba pro {ror_id} (Crossref): {e}", file=sys.stderr)
            time.sleep(REQUEST_DELAY)
    print(f"Hotovo Crossref, celkem {total_cr} záznamů.", file=sys.stderr)


if __name__ == "__main__":
    main()

