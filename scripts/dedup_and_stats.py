#!/usr/bin/env python3
import argparse
import json
import os
import sys
from collections import defaultdict
from typing import Dict, Any, List, Optional, Set


def normalize_doi(doi: Optional[str]) -> Optional[str]:
    if not doi:
        return None
    doi = doi.strip()
    low = doi.lower()
    if low.startswith("https://doi.org/"):
        doi = doi[16:]
    elif low.startswith("http://doi.org/"):
        doi = doi[15:]
    return doi.lower()


def normalize_orcid(orcid: Optional[str]) -> Optional[str]:
    if not orcid:
        return None
    val = orcid.strip()
    if val.startswith("http"):
        val = val.split("/")[-1]
    val = val.replace(" ", "")
    return val or None


def normalize_name(family: Optional[str],
                   given: Optional[str],
                   name: Optional[str]) -> Optional[str]:
    if family or given:
        fam = (family or "").strip().lower()
        giv = (given or "").strip().lower()
        key = f"{fam},{giv}".strip(",")
        return key or None
    if name:
        return name.strip().lower() or None
    return None


def author_key_from_parts(orcid: Optional[str],
                          family: Optional[str],
                          given: Optional[str],
                          name: Optional[str]) -> Optional[str]:
    """Jednoznačný klíč osoby: preferuje ORCID, jinak normalizované jméno."""
    norm_orcid = normalize_orcid(orcid)
    if norm_orcid:
        return f"orcid:{norm_orcid}"
    norm_name = normalize_name(family, given, name)
    if norm_name:
        return f"name:{norm_name}"
    return None


def normalize_ror_id(raw: Optional[str]) -> Optional[str]:
    """Z libovolného zápisu ROR (id, URL, atd.) udělá https://ror.org/xxxxx."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    if "ror.org/" in raw:
        idx = raw.find("ror.org/")
        suffix = raw[idx + len("ror.org/"):]
        suffix = suffix.strip().strip("/")
        if not suffix:
            return None
        return f"https://ror.org/{suffix}"
    # bereme poslední segment jako ROR ID
    suffix = raw.split("/")[-1].strip()
    if not suffix:
        return None
    return f"https://ror.org/{suffix}"


def authors_by_ror_from_datacite(dc_rec: Dict[str, Any]) -> Dict[str, Set[str]]:
    """
    Vrátí mapování ROR -> sada autorů (klíčů), kteří mají tento ROR
    v *per-author* affiliacích v DataCite záznamu.
    """
    mapping: Dict[str, Set[str]] = defaultdict(set)
    attrs = dc_rec.get("attributes") or {}
    people: List[Dict[str, Any]] = []
    for field in ("creators", "contributors"):
        people.extend(attrs.get(field) or [])

    for p in people:
        # identita autora
        orcid = None
        for ni in p.get("nameIdentifiers") or []:
            scheme = (ni.get("nameIdentifierScheme") or "").lower()
            if scheme == "orcid":
                orcid = ni.get("nameIdentifier")
                break
        family = p.get("familyName")
        given = p.get("givenName")
        name = p.get("name")
        key = author_key_from_parts(orcid, family, given, name)
        if not key:
            continue

        # affiliation
        affs = p.get("affiliation") or []
        if isinstance(affs, dict):
            affs = [affs]

        for aff in affs:
            cand = None
            scheme = ""
            scheme_uri = ""
            if isinstance(aff, dict):
                cand = aff.get("affiliationIdentifier") or aff.get("id") or ""
                scheme = (aff.get("affiliationIdentifierScheme") or "").lower()
                scheme_uri = (aff.get("schemeUri") or "").lower()
                if not cand:
                    # fallback – ROR může být i v name
                    cand = aff.get("name") or ""
            else:
                cand = str(aff)

            cand = cand or ""
            if not cand:
                continue

            # bereme jen to, co vypadá jako ROR
            cond = (scheme == "ror") or ("ror.org" in scheme_uri) or ("ror.org" in cand)
            if not cond:
                continue

            ror_id = normalize_ror_id(cand)
            if not ror_id:
                continue
            mapping[ror_id].add(key)

    return mapping


def authors_by_ror_from_crossref(cr_rec: Dict[str, Any]) -> Dict[str, Set[str]]:
    """
    Vrátí mapování ROR -> sada autorů (klíčů), kteří mají tento ROR
    v *per-author* affiliacích v Crossref záznamu.
    """
    mapping: Dict[str, Set[str]] = defaultdict(set)
    for a in cr_rec.get("author") or []:
        orcid = a.get("ORCID")
        family = a.get("family")
        given = a.get("given")
        key = author_key_from_parts(orcid, family, given, None)
        if not key:
            continue

        affs = a.get("affiliation") or []
        for aff in affs:
            if isinstance(aff, dict):
                cand = aff.get("id") or aff.get("name") or ""
            else:
                cand = str(aff)
            if "ror.org" not in cand:
                continue
            ror_id = normalize_ror_id(cand)
            if not ror_id:
                continue
            mapping[ror_id].add(key)
    return mapping


def load_ror_names(ror_dump_path: str,
                   country_code: str = "CZ") -> Dict[str, str]:
    """Načte ROR dump a vrátí {ror_id: display_name} pro CZ organizace."""
    with open(ror_dump_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    def is_cz(rec: Dict[str, Any]) -> bool:
        locs = rec.get("locations") or []
        for loc in locs:
            details = loc.get("geonames_details") or {}
            if details.get("country_code") == country_code:
                return True
        addrs = rec.get("addresses") or []
        for addr in addrs:
            if addr.get("country_code") == country_code:
                return True
        return False

    def display_name(rec: Dict[str, Any]) -> str:
        ror_id = rec.get("id", "")
        names = rec.get("names") or []
        for n in names:
            types = n.get("types") or []
            if "ror_display" in types:
                return n.get("value") or ror_id
        if names:
            return names[0].get("value") or ror_id
        return ror_id

    mapping: Dict[str, str] = {}
    for rec in data:
        if is_cz(rec):
            rid = rec.get("id")
            if rid:
                mapping[rid] = display_name(rec)
    return mapping


def main():
    parser = argparse.ArgumentParser(
        description="Deduplicate harvested DataCite/Crossref datasets and compute stats."
    )
    parser.add_argument("--datacite", required=True,
                        help="Path to datacite_cz_datasets.jsonl")
    parser.add_argument("--crossref", required=True,
                        help="Path to crossref_cz_datasets.jsonl")
    parser.add_argument("--ror-dump", required=True,
                        help="Path to ROR JSON dump (for institution names)")
    parser.add_argument("--out-dir", default="processed",
                        help="Output directory (default: ./processed)")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    dedup_path = os.path.join(args.out_dir, "datasets_dedup.jsonl")
    summary_path = os.path.join(args.out_dir, "summary_stats.json")
    inst_path = os.path.join(args.out_dir, "institutions.tsv")

    print(f"Načítám ROR dump z {args.ror_dump} ...", file=sys.stderr)
    ror_names = load_ror_names(args.ror_dump)
    print(f"Nalezeno {len(ror_names)} CZ institucí v ROR.", file=sys.stderr)

    # DOI → agregovaný záznam
    doi_index: Dict[str, Dict[str, Any]] = {}
    raw_counts = {"datacite": 0, "crossref": 0}

    def add_record(source: str, rec: Dict[str, Any]):
        raw_counts[source] += 1
        doi_raw = rec.get("doi")
        doi_norm = normalize_doi(doi_raw)
        if not doi_norm:
            return
        ror_id = rec.get("ror_id")

        agg = doi_index.get(doi_norm)
        if not agg:
            agg = {
                "doi": doi_norm,
                "sources": set(),
                "ror_ids": set(),
                "records": {"datacite": None, "crossref": None},
            }
            doi_index[doi_norm] = agg

        agg["sources"].add(source)
        if ror_id:
            agg["ror_ids"].add(ror_id)

        if source == "datacite":
            agg["records"]["datacite"] = rec.get("record")
        elif source == "crossref":
            agg["records"]["crossref"] = rec.get("record")

    print(f"Čtu DataCite z {args.datacite} ...", file=sys.stderr)
    with open(args.datacite, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("source") != "datacite":
                continue
            add_record("datacite", rec)

    print(f"Čtu Crossref z {args.crossref} ...", file=sys.stderr)
    with open(args.crossref, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("source") != "crossref":
                continue
            add_record("crossref", rec)

    print(f"Celkem unikátních DOI po deduplikaci: {len(doi_index)}", file=sys.stderr)

    total_unique = len(doi_index)
    overlap = sum(
        1
        for agg in doi_index.values()
        if "datacite" in agg["sources"] and "crossref" in agg["sources"]
    )

    # počet datasetů na instituci (podle dataset-affiliací = ROR v dotazu)
    inst_dataset_counts: Dict[str, int] = defaultdict(int)
    for agg in doi_index.values():
        for ror_id in agg["ror_ids"]:
            inst_dataset_counts[ror_id] += 1

    # nový výpočet: autoři na instituci jen pokud mají daný ROR v author-affiliation
    inst_authors: Dict[str, Set[str]] = defaultdict(set)

    for agg in doi_index.values():
        ror_ids = agg["ror_ids"]
        dc_rec = agg["records"]["datacite"]
        cr_rec = agg["records"]["crossref"]

        # pro daný DOI: ROR -> sada autorů (z DC i CR)
        authors_by_ror: Dict[str, Set[str]] = defaultdict(set)

        if dc_rec:
            dc_map = authors_by_ror_from_datacite(dc_rec)
            for rid, authors in dc_map.items():
                authors_by_ror[rid].update(authors)
        if cr_rec:
            cr_map = authors_by_ror_from_crossref(cr_rec)
            for rid, authors in cr_map.items():
                authors_by_ror[rid].update(authors)

        if not authors_by_ror:
            continue

        # záleží nám jen na CZ ROR, které jsou v agg["ror_ids"]
        for ror_id in ror_ids:
            authors_for_inst = authors_by_ror.get(ror_id)
            if not authors_for_inst:
                continue
            inst_authors[ror_id].update(authors_for_inst)

    print(f"Zapisuji deduplikovaná data do {dedup_path} ...", file=sys.stderr)
    with open(dedup_path, "w", encoding="utf-8") as out_f:
        for agg in doi_index.values():
            out_obj = {
                "doi": agg["doi"],
                "sources": sorted(agg["sources"]),
                "ror_ids": sorted(agg["ror_ids"]),
                "records": agg["records"],
            }
            out_f.write(json.dumps(out_obj, ensure_ascii=False) + "\n")

    summary = {
        "raw_counts": raw_counts,
        "unique_doi": total_unique,
        "overlap_doi": overlap,
        "institution_count": len([r for r, c in inst_dataset_counts.items() if c > 0]),
    }
    print(f"Zapisuji summary do {summary_path} ...", file=sys.stderr)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"Zapisuji tabulku institucí do {inst_path} ...", file=sys.stderr)
    with open(inst_path, "w", encoding="utf-8") as f:
        f.write("ror_id\tname\tdataset_count\tauthor_count\n")
        for ror_id, ds_count in sorted(
            inst_dataset_counts.items(), key=lambda kv: kv[1], reverse=True
        ):
            if ds_count == 0:
                continue
            author_count = len(inst_authors.get(ror_id, set()))
            name = ror_names.get(ror_id, "")
            f.write(f"{ror_id}\t{name}\t{ds_count}\t{author_count}\n")


    print("Hotovo.", file=sys.stderr)


if __name__ == "__main__":
    main()
