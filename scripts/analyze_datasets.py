#!/usr/bin/env python3
"""
analyze_datasets.py

Analyzuje deduplikovaný soubor datasets_dedup*.jsonl a vytváří agregace
+ plochý CSV výpis.

Výstupy v adresáři --out-dir:
- timeline.tsv                  (datasets podle roku)
- repos_datacite.tsv            (DataCite client/publisher/resourceTypeGeneral)
- repos_crossref.tsv            (Crossref member/publisher)
- orcid_coverage.json           (souhrn ORCID coverage)
- orcid_by_institution.tsv      (ORCID coverage podle institucí – z author-affiliations)
- funders_datacite.tsv          (fundery z DataCite)
- funders_crossref.tsv          (fundery z Crossref)
- licenses_datacite.tsv         (licence podle rightsUri atd.)
- license_dataset_summary.json  (dataset-level open/closed/none)
- datasets_flat.csv             (plochý export pro Excel)

Volitelně:
- --institutions-out            (TSV s dataset_count / author_count podle institucí;
                                 počítáno z author-affiliations a deduplikovaných DOI)
"""

import argparse
import csv
import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple


# --- Pomocné funkce ---------------------------------------------------------


def normalize_orcid(orcid: Optional[str]) -> Optional[str]:
    if not orcid:
        return None
    val = orcid.strip()
    if val.startswith("http"):
        val = val.split("/")[-1]
    val = val.replace(" ", "")
    return val or None


def normalize_name(
    family: Optional[str], given: Optional[str], name: Optional[str]
) -> Optional[str]:
    """
    Normalizace jména pro použití v klíči osoby (lowercase, 'family,given').
    """
    if family or given:
        fam = (family or "").strip().lower()
        giv = (given or "").strip().lower()
        key = f"{fam},{giv}".strip(",")
        return key or None
    if name:
        return (name or "").strip().lower() or None
    return None


def author_key_from_parts(
    orcid: Optional[str],
    family: Optional[str],
    given: Optional[str],
    name: Optional[str],
) -> Optional[str]:
    """
    Jednoznačný klíč osoby: preferuje ORCID, jinak normalizované jméno.
    """
    norm_orcid = normalize_orcid(orcid)
    if norm_orcid:
        return f"orcid:{norm_orcid}"
    norm_name = normalize_name(family, given, name)
    if norm_name:
        return f"name:{norm_name}"
    return None


def normalize_ror_id(raw: Optional[str]) -> Optional[str]:
    """
    Z libovolného zápisu ROR (id, URL, atd.) udělá https://ror.org/xxxxx.
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None

    if "ror.org/" in raw:
        idx = raw.find("ror.org/")
        suffix = raw[idx + len("ror.org/") :]
        suffix = suffix.strip().strip("/")
        if not suffix:
            return None
        return f"https://ror.org/{suffix}"

    # bereme poslední segment jako ROR ID
    suffix = raw.split("/")[-1].strip()
    if not suffix:
        return None
    return f"https://ror.org/{suffix}"


def author_ids_from_datacite(
    dc_rec: Dict[str, Any]
) -> Tuple[Set[str], Set[str]]:
    """
    Vrátí (všichni autoři, autoři s ORCID) pro DataCite záznam.

    Klíče osob mají tvar:
      - "orcid:0000-0002-1825-0097"
      - "name:prijmeni,jmeno"
    """
    all_authors: Set[str] = set()
    orcid_authors: Set[str] = set()

    attrs = dc_rec.get("attributes") or {}
    people: List[Dict[str, Any]] = []
    for field in ("creators", "contributors"):
        people.extend(attrs.get(field) or [])

    for p in people:
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

        all_authors.add(key)
        if key.startswith("orcid:"):
            orcid_authors.add(key)

    return all_authors, orcid_authors


def author_ids_from_crossref(
    cr_rec: Dict[str, Any]
) -> Tuple[Set[str], Set[str]]:
    """
    Vrátí (všichni autoři, autoři s ORCID) pro Crossref záznam.
    """
    all_authors: Set[str] = set()
    orcid_authors: Set[str] = set()

    for a in cr_rec.get("author") or []:
        orcid = a.get("ORCID")
        family = a.get("family")
        given = a.get("given")
        name = None

        key = author_key_from_parts(orcid, family, given, name)
        if not key:
            continue

        all_authors.add(key)
        if key.startswith("orcid:"):
            orcid_authors.add(key)

    return all_authors, orcid_authors


def authors_by_ror_from_datacite(dc_rec: Dict[str, Any]) -> Dict[str, Set[str]]:
    """
    Vrátí mapování ROR -> sada autorů (klíčů), kteří mají tento ROR v
    *per-author* affiliacích v DataCite záznamu.
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
            cond = (scheme == "ror") or ("ror.org" in scheme_uri) or (
                "ror.org" in cand
            )
            if not cond:
                continue

            ror_id = normalize_ror_id(cand)
            if not ror_id:
                continue

            mapping[ror_id].add(key)

    return mapping


def authors_by_ror_from_crossref(cr_rec: Dict[str, Any]) -> Dict[str, Set[str]]:
    """
    Vrátí mapování ROR -> sada autorů (klíčů), kteří mají tento ROR v
    *per-author* affiliacích v Crossref záznamu.
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

            cand = cand or ""
            if not cand:
                continue

            if "ror.org" not in cand:
                continue

            ror_id = normalize_ror_id(cand)
            if not ror_id:
                continue

            mapping[ror_id].add(key)

    return mapping


def extract_year(
    dc_rec: Optional[Dict[str, Any]], cr_rec: Optional[Dict[str, Any]]
) -> Optional[int]:
    """
    Zkusí vytáhnout rok: DataCite.publicationYear, jinak Crossref.issued.
    """
    year: Optional[int] = None

    if dc_rec:
        attrs = dc_rec.get("attributes") or {}
        y = attrs.get("publicationYear")
        if isinstance(y, int):
            year = y
        elif isinstance(y, str) and y.isdigit():
            year = int(y)

    if year is None and cr_rec:
        issued = cr_rec.get("issued") or {}
        parts = issued.get("date-parts") or []
        if parts and parts[0]:
            y = parts[0][0]
            if isinstance(y, int):
                year = y
            elif isinstance(y, str) and y.isdigit():
                year = int(y)

    return year


def datacite_client(dc_rec: Dict[str, Any]) -> Optional[str]:
    rel = dc_rec.get("relationships") or {}
    client = rel.get("client") or {}
    data = client.get("data") or {}
    return data.get("id")  # např. "cern.zenodo"


def datacite_publisher(dc_rec: Dict[str, Any]) -> Optional[str]:
    attrs = dc_rec.get("attributes") or {}
    return attrs.get("publisher")


def datacite_resource_type(dc_rec: Dict[str, Any]) -> Optional[str]:
    attrs = dc_rec.get("attributes") or {}
    types = attrs.get("types") or {}
    return types.get("resourceTypeGeneral")


def is_open_license(
    uri: Optional[str], ident: Optional[str], name: Optional[str]
) -> bool:
    """
    Velmi jednoduchý heuristický test, jestli jde o otevřenou licenci.
    """
    text = " ".join([s.lower() for s in [uri or "", ident or "", name or ""]])
    if any(
        pat in text
        for pat in [
            "creativecommons.org",
            "cc-by",
            "cc0",
            "cc by",
            "pddl",
            "odbl",
            "opendatacommons.org",
        ]
    ):
        return True
    return False


def load_ror_names(ror_dump_path: str) -> Dict[str, str]:
    """
    Načte ROR dump a vrátí {ror_id: display_name} (bez ohledu na zemi).
    """
    with open(ror_dump_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    mapping: Dict[str, str] = {}
    for rec in data:
        rid = rec.get("id")
        if not rid:
            continue
        names = rec.get("names") or []
        disp = rid
        for n in names:
            types = n.get("types") or []
            if "ror_display" in types:
                disp = n.get("value") or rid
                break
        if disp == rid and names:
            disp = names[0].get("value") or rid
        mapping[rid] = disp
    return mapping


# --- Hlavní funkce ----------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze datasets_dedup.jsonl and compute aggregates."
    )
    parser.add_argument(
        "--dedup",
        required=True,
        help="Path to datasets_dedup.jsonl (or datasets_dedup_zenodo_concepts.jsonl)",
    )
    parser.add_argument(
        "--out-dir",
        default="analysis",
        help="Output directory (default: ./analysis)",
    )
    parser.add_argument(
        "--ror-dump",
        help="Optional path to ROR dump (for institution names in ORCID stats / institutions-out)",
    )
    parser.add_argument(
        "--institutions-out",
        help=(
            "Optional path to TSV with institution-level counts "
            "(ror_id, name, dataset_count, author_count) based on author affiliations."
        ),
    )
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    # Agregace
    timeline_total: Dict[int, int] = defaultdict(int)
    timeline_datacite: Dict[int, int] = defaultdict(int)
    timeline_crossref: Dict[int, int] = defaultdict(int)

    repos_datacite: Dict[Tuple[str, str, str], int] = defaultdict(
        int
    )  # (client_id, publisher, resourceTypeGeneral) -> count
    repos_crossref: Dict[Tuple[str, str], int] = defaultdict(
        int
    )  # (member, publisher) -> count

    # ORCID coverage (globální)
    datasets_total = 0
    datasets_with_orcid = 0
    all_authors_global: Set[str] = set()
    authors_with_orcid_global: Set[str] = set()

    # Instituce – podle author-affiliations + ROR
    inst_dataset_counts: Dict[str, int] = defaultdict(int)
    inst_authors_all: Dict[str, Set[str]] = defaultdict(set)
    inst_authors_orcid: Dict[str, Set[str]] = defaultdict(set)

    # Funders
    funders_datacite: Dict[Tuple[str, str, str], int] = defaultdict(
        int
    )  # (identifier, type, name) -> count
    funders_crossref: Dict[Tuple[str, str], int] = defaultdict(
        int
    )  # (doi, name) -> count

    # Licence
    licenses_datacite: Dict[Tuple[str, str, str, bool], int] = defaultdict(
        int
    )  # (uri, ident, rights, open_flag) -> count
    license_dataset_summary: Dict[str, int] = {"open": 0, "nonopen": 0, "none": 0}

    # CSV flattening – připravíme hlavičku
    flat_csv_path = os.path.join(args.out_dir, "datasets_flat.csv")
    flat_csv_fh = open(flat_csv_path, "w", encoding="utf-8", newline="")
    flat_writer = csv.writer(flat_csv_fh)
    flat_writer.writerow(
        [
            "doi",
            "sources",
            "ror_ids",
            "year",
            "datacite_client_id",
            "datacite_publisher",
            "datacite_resourceTypeGeneral",
            "datacite_title",
            "datacite_licenses",
            "crossref_member",
            "crossref_publisher",
            "crossref_year",
            "crossref_title",
            "n_authors_total",
            "n_authors_with_orcid",
        ]
    )

    # ROR names (pro ORCID coverage / institutions-out podle instituce)
    ror_names: Dict[str, str] = {}
    if args.ror_dump:
        ror_names = load_ror_names(args.ror_dump)

    # --- Čtení deduplikovaných záznamů ---
    with open(args.dedup, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            obj = json.loads(line)
            doi = obj.get("doi")
            sources = set(obj.get("sources") or [])
            ror_ids_for_dataset: List[str] = obj.get("ror_ids") or []
            # pro jistotu odstraníme duplicity
            ror_ids_for_dataset = sorted(set(ror_ids_for_dataset))

            recs = obj.get("records") or {}
            dc = recs.get("datacite")
            cr = recs.get("crossref")

            datasets_total += 1

            # --- Rok a timeline ---
            year = extract_year(dc, cr)
            if year is not None:
                timeline_total[year] += 1
                if "datacite" in sources:
                    timeline_datacite[year] += 1
                if "crossref" in sources:
                    timeline_crossref[year] += 1

            # --- Repozitáře / publisher + licence + funders ---
            dc_client_id: Optional[str] = None
            dc_publisher: Optional[str] = None
            dc_rtype: Optional[str] = None
            dc_title: Optional[str] = None
            dc_licenses_list: List[str] = []

            cr_member: Optional[str] = None
            cr_publisher: Optional[str] = None
            cr_title: Optional[str] = None
            cr_year: Optional[int] = None

            if dc:
                dc_client_id = datacite_client(dc)
                dc_publisher = datacite_publisher(dc)
                dc_rtype = datacite_resource_type(dc)

                attrs = dc.get("attributes") or {}

                titles = attrs.get("titles") or []
                if titles:
                    dc_title = titles[0].get("title")

                # rightsList → licence
                rights_list = attrs.get("rightsList") or []
                has_open = False
                has_any_rights = bool(rights_list)

                for r in rights_list:
                    uri = r.get("rightsUri")
                    ident = r.get("rightsIdentifier")
                    rights = r.get("rights")
                    open_flag = is_open_license(uri, ident, rights)
                    if open_flag:
                        has_open = True

                    key_license = (
                        uri or "",
                        ident or "",
                        rights or "",
                        open_flag,
                    )
                    licenses_datacite[key_license] += 1

                    # pro plochý CSV: label = uri or ident or rights
                    label = uri or ident or rights
                    if label and label not in dc_licenses_list:
                        dc_licenses_list.append(label)

                if has_any_rights:
                    if has_open:
                        license_dataset_summary["open"] += 1
                    else:
                        license_dataset_summary["nonopen"] += 1
                else:
                    license_dataset_summary["none"] += 1

                # funders (DataCite)
                for fr in attrs.get("fundingReferences") or []:
                    fid = fr.get("funderIdentifier") or ""
                    ftype = fr.get("funderIdentifierType") or ""
                    fname = fr.get("funderName") or ""
                    key_funder_dc = (fid, ftype, fname)
                    funders_datacite[key_funder_dc] += 1

                key_repo_dc = (dc_client_id or "", dc_publisher or "", dc_rtype or "")
                repos_datacite[key_repo_dc] += 1

            if cr:
                cr_member = cr.get("member")
                cr_publisher = cr.get("publisher")

                titles = cr.get("title") or []
                if titles:
                    cr_title = titles[0]

                issued = cr.get("issued") or {}
                parts = issued.get("date-parts") or []
                if parts and parts[0]:
                    y = parts[0][0]
                    if isinstance(y, int):
                        cr_year = y
                    elif isinstance(y, str) and y.isdigit():
                        cr_year = int(y)

                # funders (Crossref)
                for fr in cr.get("funder") or []:
                    fdoi = fr.get("DOI") or ""
                    fname = fr.get("name") or ""
                    key_funder_cr = (fdoi, fname)
                    funders_crossref[key_funder_cr] += 1

                key_repo_cr = (str(cr_member or ""), cr_publisher or "")
                repos_crossref[key_repo_cr] += 1

            # --- Autoři / ORCID coverage (globální) ---
            authors_all_dc: Set[str] = set()
            authors_orcid_dc: Set[str] = set()
            authors_all_cr: Set[str] = set()
            authors_orcid_cr: Set[str] = set()

            if dc:
                authors_all_dc, authors_orcid_dc = author_ids_from_datacite(dc)
            if cr:
                authors_all_cr, authors_orcid_cr = author_ids_from_crossref(cr)

            authors_all = authors_all_dc | authors_all_cr
            authors_orcid = authors_orcid_dc | authors_orcid_cr

            all_authors_global.update(authors_all)
            authors_with_orcid_global.update(authors_orcid)
            if authors_orcid:
                datasets_with_orcid += 1

            # --- Instituce (ROR) podle author-affiliations ---
            authors_by_ror: Dict[str, Set[str]] = defaultdict(set)
            if dc:
                dc_map = authors_by_ror_from_datacite(dc)
                for rid, auths in dc_map.items():
                    authors_by_ror[rid].update(auths)
            if cr:
                cr_map = authors_by_ror_from_crossref(cr)
                for rid, auths in cr_map.items():
                    authors_by_ror[rid].update(auths)

            if authors_by_ror:
                # Zajímá nás průnik ROR z harvestu (obj["ror_ids"]) a ROR z author-affiliations.
                # Pokud by ror_ids nebyly k dispozici, použijeme všechny z authors_by_ror.
                if ror_ids_for_dataset:
                    relevant_ror_ids = [
                        rid
                        for rid in ror_ids_for_dataset
                        if rid in authors_by_ror
                    ]
                else:
                    relevant_ror_ids = list(authors_by_ror.keys())

                for rid in relevant_ror_ids:
                    authors_for_inst = authors_by_ror.get(rid)
                    if not authors_for_inst:
                        continue

                    # dataset se pro instituci počítá právě jednou
                    inst_dataset_counts[rid] += 1

                    # autoři – každý autor je pro instituci započítán maximálně jednou
                    inst_authors_all[rid].update(authors_for_inst)
                    for akey in authors_for_inst:
                        if akey.startswith("orcid:"):
                            inst_authors_orcid[rid].add(akey)

            # --- Zápis do plochého CSV ---
            flat_writer.writerow(
                [
                    doi or "",
                    ";".join(sorted(sources)),
                    ";".join(ror_ids_for_dataset),
                    year if year is not None else "",
                    dc_client_id or "",
                    dc_publisher or "",
                    dc_rtype or "",
                    dc_title or "",
                    ";".join(dc_licenses_list),
                    cr_member or "",
                    cr_publisher or "",
                    cr_year if cr_year is not None else "",
                    cr_title or "",
                    len(authors_all),
                    len(authors_orcid),
                ]
            )

    flat_csv_fh.close()

    # --- Uložení agregací ---

    # 1) timeline.tsv
    timeline_path = os.path.join(args.out_dir, "timeline.tsv")
    with open(timeline_path, "w", encoding="utf-8") as f:
        f.write("year\ttotal\tdatacite\tcrossref\n")
        for y in sorted(timeline_total.keys()):
            f.write(
                f"{y}\t{timeline_total[y]}"
                f"\t{timeline_datacite.get(y, 0)}"
                f"\t{timeline_crossref.get(y, 0)}\n"
            )

    # 2) repos_datacite.tsv
    repos_dc_path = os.path.join(args.out_dir, "repos_datacite.tsv")
    with open(repos_dc_path, "w", encoding="utf-8") as f:
        f.write("client_id\tpublisher\tresourceTypeGeneral\tdataset_count\n")
        for (cid, pub, rtype), c in sorted(
            repos_datacite.items(), key=lambda kv: kv[1], reverse=True
        ):
            f.write(f"{cid}\t{pub}\t{rtype}\t{c}\n")

    # 3) repos_crossref.tsv
    repos_cr_path = os.path.join(args.out_dir, "repos_crossref.tsv")
    with open(repos_cr_path, "w", encoding="utf-8") as f:
        f.write("member\tpublisher\tdataset_count\n")
        for (member, pub), c in sorted(
            repos_crossref.items(), key=lambda kv: kv[1], reverse=True
        ):
            f.write(f"{member}\t{pub}\t{c}\n")

    # 4) orcid_coverage.json
    orcid_cov = {
        "datasets_total": datasets_total,
        "datasets_with_at_least_one_orcid": datasets_with_orcid,
        "datasets_with_at_least_one_orcid_pct": (
            datasets_with_orcid / datasets_total * 100.0
            if datasets_total
            else 0.0
        ),
        "persons_total": len(all_authors_global),
        "persons_with_orcid": len(authors_with_orcid_global),
        "persons_with_orcid_pct": (
            len(authors_with_orcid_global) / len(all_authors_global) * 100.0
            if all_authors_global
            else 0.0
        ),
    }
    with open(
        os.path.join(args.out_dir, "orcid_coverage.json"),
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(orcid_cov, f, ensure_ascii=False, indent=2)

    # 5) orcid_by_institution.tsv
    orcid_inst_path = os.path.join(args.out_dir, "orcid_by_institution.tsv")
    with open(orcid_inst_path, "w", encoding="utf-8") as f:
        f.write(
            "ror_id\tname\tpersons_total\tpersons_with_orcid\tpersons_with_orcid_pct\n"
        )
        for rid, authors_all_inst in sorted(
            inst_authors_all.items(), key=lambda kv: len(kv[1]), reverse=True
        ):
            authors_orcid_inst = inst_authors_orcid.get(rid, set())
            total = len(authors_all_inst)
            with_orcid = len(authors_orcid_inst)
            pct = (with_orcid / total * 100.0) if total else 0.0
            name = ror_names.get(rid, "")
            f.write(f"{rid}\t{name}\t{total}\t{with_orcid}\t{pct:.2f}\n")

    # 6) funders_datacite.tsv
    funders_dc_path = os.path.join(args.out_dir, "funders_datacite.tsv")
    with open(funders_dc_path, "w", encoding="utf-8") as f:
        f.write(
            "funderIdentifier\tfunderIdentifierType\tfunderName\tdataset_count\n"
        )
        for (fid, ftype, fname), c in sorted(
            funders_datacite.items(), key=lambda kv: kv[1], reverse=True
        ):
            f.write(f"{fid}\t{ftype}\t{fname}\t{c}\n")

    # 7) funders_crossref.tsv
    funders_cr_path = os.path.join(args.out_dir, "funders_crossref.tsv")
    with open(funders_cr_path, "w", encoding="utf-8") as f:
        f.write("funderDOI\tfunderName\tdataset_count\n")
        for (fdoi, fname), c in sorted(
            funders_crossref.items(), key=lambda kv: kv[1], reverse=True
        ):
            f.write(f"{fdoi}\t{fname}\t{c}\n")

    # 8) licenses_datacite.tsv
    licenses_path = os.path.join(args.out_dir, "licenses_datacite.tsv")
    with open(licenses_path, "w", encoding="utf-8") as f:
        f.write("rightsUri\trightsIdentifier\trights\tis_open\tdataset_count\n")
        for (uri, ident, rights, open_flag), c in sorted(
            licenses_datacite.items(), key=lambda kv: kv[1], reverse=True
        ):
            f.write(f"{uri}\t{ident}\t{rights}\t{int(open_flag)}\t{c}\n")

    # 9) license_dataset_summary.json
    with open(
        os.path.join(args.out_dir, "license_dataset_summary.json"),
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(license_dataset_summary, f, ensure_ascii=False, indent=2)

    # 10) institutions-out (volitelné, podle author-affiliations)
    if args.institutions_out:
        inst_out_path = args.institutions_out
        inst_dir = os.path.dirname(inst_out_path)
        if inst_dir:
            os.makedirs(inst_dir, exist_ok=True)

        with open(inst_out_path, "w", encoding="utf-8") as f:
            f.write("ror_id\tname\tdataset_count\tauthor_count\n")
            for rid, ds_count in sorted(
                inst_dataset_counts.items(), key=lambda kv: kv[1], reverse=True
            ):
                if ds_count == 0:
                    continue
                author_count = len(inst_authors_all.get(rid, set()))
                name = ror_names.get(rid, "")
                f.write(f"{rid}\t{name}\t{ds_count}\t{author_count}\n")

    print("Hotovo. Výstupy najdeš v adresáři:", args.out_dir)


if __name__ == "__main__":
    main()

