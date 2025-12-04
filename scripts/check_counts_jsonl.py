#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Set, Dict, Any


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_PROCESSED = BASE_DIR / "data" / "processed"


def extract_rors_from_datacite(dc: Dict[str, Any]) -> Set[str]:
    rors: Set[str] = set()
    if not isinstance(dc, dict):
        return rors

    # často bývá v poli "creators" / "contributors"
    for key in ("creators", "contributors"):
        items = dc.get(key) or []
        if not isinstance(items, list):
            continue
        for person in items:
            affs = person.get("affiliation") or []
            # affiliation může být list dictů nebo list stringů
            if isinstance(affs, list):
                for aff in affs:
                    if isinstance(aff, dict):
                        ident = aff.get("affiliationIdentifier") or ""
                        scheme = (aff.get("affiliationIdentifierScheme") or "").upper()
                        if ident and ("ror.org" in ident or scheme == "ROR"):
                            rors.add(ident.strip())
                    elif isinstance(aff, str):
                        if "ror.org" in aff:
                            rors.add(aff.strip())

    # některé implementace mají přímo "affiliations" na rootu
    affs_root = dc.get("affiliations") or []
    if isinstance(affs_root, list):
        for aff in affs_root:
            if isinstance(aff, dict):
                ident = aff.get("affiliationIdentifier") or ""
                scheme = (aff.get("affiliationIdentifierScheme") or "").upper()
                if ident and ("ror.org" in ident or scheme == "ROR"):
                    rors.add(ident.strip())
            elif isinstance(aff, str):
                if "ror.org" in aff:
                    rors.add(aff.strip())

    return rors


def extract_rors_from_crossref(cr: Dict[str, Any]) -> Set[str]:
    rors: Set[str] = set()
    if not isinstance(cr, dict):
        return rors

    authors = cr.get("author") or []
    if isinstance(authors, list):
        for a in authors:
            affs = a.get("affiliation") or []
            if not isinstance(affs, list):
                continue
            for aff in affs:
                # Crossref affiliation obvykle nemá ROR, ale kdyby náhodou:
                if isinstance(aff, dict):
                    # někdy mívá 'id' s ROR URL
                    ident = aff.get("id") or ""
                    if "ror.org" in ident:
                        rors.add(ident.strip())
                    name = aff.get("name") or ""
                    if "ror.org" in name:
                        rors.add(name.strip())
                elif isinstance(aff, str):
                    if "ror.org" in aff:
                        rors.add(aff.strip())
    return rors


def extract_rors(record: Dict[str, Any]) -> Set[str]:
    """
    Nezávislá, primitivní extrakce ROR ID z jednoho záznamu.
    - nebere v potaz žádné předpočítané 'ror_ids' ve flattenovaných datech.
    - snaží se najít 'ror.org' v původních metadatech.
    """
    rors: Set[str] = set()

    # Kdyby přece jen existovalo top-level pole 'ror_ids', ignorovat nechceš,
    # ale můžeme ho brát jen jako doplněk.
    ror_ids = record.get("ror_ids")
    if isinstance(ror_ids, list):
        for r in ror_ids:
            if isinstance(r, str) and "ror.org" in r:
                rors.add(r.strip())
    elif isinstance(ror_ids, str):
        for part in ror_ids.split(";"):
            part = part.strip()
            if "ror.org" in part:
                rors.add(part)

    # DataCite podklíč může být 'datacite' nebo 'datacite_record' atd.
    for key in ("datacite", "datacite_record", "datacite_metadata"):
        if key in record:
            rors |= extract_rors_from_datacite(record[key])

    # Crossref podklíč
    for key in ("crossref", "crossref_record", "crossref_metadata"):
        if key in record:
            rors |= extract_rors_from_crossref(record[key])

    # Pro jistotu projdeme i top-level stringová pole, jestli se tam někde neprovlékne "ror.org"
    for k, v in record.items():
        if isinstance(v, str) and "ror.org" in v:
            rors.add(v.strip())

    return rors


def extract_doi(record: Dict[str, Any]) -> str:
    """
    Snaží se najít DOI v záznamu. Nezávislé na flattenu.
    """
    if "doi" in record and isinstance(record["doi"], str):
        return record["doi"].strip()

    # DataCite
    dc = record.get("datacite") or record.get("datacite_record") or {}
    if isinstance(dc, dict):
        if isinstance(dc.get("doi"), str):
            return dc["doi"].strip()
        # možná 'id' nebo 'attributes' -> 'doi'
        attrs = dc.get("attributes")
        if isinstance(attrs, dict):
            if isinstance(attrs.get("doi"), str):
                return attrs["doi"].strip()

    # Crossref
    cr = record.get("crossref") or record.get("crossref_record") or {}
    if isinstance(cr, dict):
        if isinstance(cr.get("DOI"), str):
            return cr["DOI"].strip()

    return ""


def check_file(jsonl_path: Path, ror: str, show_examples: int) -> None:
    """
    Projde daný .jsonl soubor a spočítá, kolik záznamů nese daný ROR.
    Volitelně vypíše pár příkladových DOI.
    """
    total_records = 0
    hit_records = 0
    examples = []

    with jsonl_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total_records += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            rors = extract_rors(rec)
            if ror in rors:
                hit_records += 1
                if len(examples) < show_examples:
                    examples.append(extract_doi(rec))

    print(f"Soubor: {jsonl_path}")
    print(f"  Celkem záznamů:   {total_records}")
    print(f"  Záznamů s ROR {ror}: {hit_records}")
    if examples:
        print(f"  Příklady DOI (max {show_examples}):")
        for d in examples:
            if d:
                print("    -", d)
    print()


def main():
    ap = argparse.ArgumentParser(
        description="Primitivní kontrola počtu záznamů pro daný ROR přímo z deduplikovaného JSONL."
    )
    ap.add_argument(
        "--ror",
        required=True,
        help="ROR ID, např. https://ror.org/024d6js02",
    )
    ap.add_argument(
        "--mode",
        choices=["doi", "concepts", "both"],
        default="both",
        help="Který JSONL procházet: DOI (datasets_dedup.jsonl), "
             "concepts (datasets_dedup_zenodo_concepts.jsonl), nebo oba.",
    )
    ap.add_argument(
        "--show-examples",
        type=int,
        default=5,
        help="Kolik příkladových DOI vypsat pro daný ROR.",
    )
    args = ap.parse_args()

    if args.mode in ("doi", "both"):
        path = DATA_PROCESSED / "datasets_dedup.jsonl"
        check_file(path, args.ror, args.show_examples)

    if args.mode in ("concepts", "both"):
        path = DATA_PROCESSED / "datasets_dedup_zenodo_concepts.jsonl"
        if path.exists():
            check_file(path, args.ror, args.show_examples)
        else:
            print(f"Soubor {path} neexistuje, concepts režim přeskočen.")


if __name__ == "__main__":
    main()

