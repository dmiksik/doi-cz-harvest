#!/usr/bin/env python3
import argparse
import json
import sys
import re
from collections import defaultdict
from pathlib import Path

ZENODO_PREFIX = "10.5281/zenodo"


def normalize_doi(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    # odříznout případné URL
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.IGNORECASE)
    return s.lower()


def is_zenodo_doi(doi_norm: str) -> bool:
    return ZENODO_PREFIX in (doi_norm or "")


def load_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def dump_jsonl(path: Path, records):
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def get_datacite_payload(rec: dict):
    """
    Vrátí DataCite payload z deduplikovaného záznamu.
    Přizpůsob podle toho, jak strukturuješ datasets_dedup.jsonl.
    """
    # typická varianta: rec["records"]["datacite"] obsahuje DataCite JSON
    records = rec.get("records") or {}
    dc = records.get("datacite")
    # fallback: někdo ukládá přímo pod "datacite"
    if dc is None:
        dc = rec.get("datacite")
    return dc


def find_zenodo_concepts_and_versions(records):
    """
    První průchod:
    - najdeme concept DOIs (HasVersion -> Zenodo DOI)
    - a mapu concept_doi -> [version_candidate_doi_norm, ...]
    """
    concept_dois = set()
    concept_to_versions = defaultdict(list)

    for rec in records:
        doi_norm = normalize_doi(rec.get("doi"))
        dc = get_datacite_payload(rec)
        if not dc:
            continue

        attrs = dc.get("attributes", dc)

        # jen Zenodo záznamy
        publisher_name = ""
        if isinstance(attrs.get("publisher"), dict):
            publisher_name = (attrs["publisher"].get("name") or "").lower()
        else:
            publisher_name = (attrs.get("publisher") or "").lower()

        client_id = (attrs.get("clientId") or attrs.get("client-id") or "").lower()
        if not (is_zenodo_doi(doi_norm) or "zenodo" in client_id or "zenodo" in publisher_name):
            continue

        rels = attrs.get("relatedIdentifiers", []) or []
        # je to concept record, pokud má HasVersion na Zenodo DOI
        has_version_targets = []
        for rel in rels:
            if (
                rel.get("relationType") == "HasVersion"
                and rel.get("relatedIdentifierType") == "DOI"
            ):
                rid_norm = normalize_doi(rel.get("relatedIdentifier"))
                if is_zenodo_doi(rid_norm):
                    has_version_targets.append(rid_norm)

        if has_version_targets:
            concept_dois.add(doi_norm)
            for rid_norm in has_version_targets:
                concept_to_versions[doi_norm].append(rid_norm)

    return concept_dois, concept_to_versions


def collapse_zenodo_versions(
    input_path: Path, output_path: Path, log_path: Path | None = None
):
    # načteme si všechny záznamy do paměti (7097 DOI je OK)
    records = list(load_jsonl(input_path))

    # index DOI -> záznam
    doi_index = {}
    for rec in records:
        doi_norm = normalize_doi(rec.get("doi"))
        if not doi_norm:
            continue
        if doi_norm in doi_index:
            # nemělo by nastat po deduplikaci; kdyby ano, přepíší se
            pass
        doi_index[doi_norm] = rec

    concept_dois, concept_to_versions = find_zenodo_concepts_and_versions(records)

    zenodo_versions_to_drop = set()
    log_lines = []

    for concept_doi in concept_dois:
        version_candidates = concept_to_versions.get(concept_doi, [])
        for version_doi in version_candidates:
            rec_v = doi_index.get(version_doi)
            if not rec_v:
                log_lines.append(
                    (
                        "missing_version_record",
                        concept_doi,
                        version_doi,
                        "HasVersion points to DOI not present in datasets_dedup",
                    )
                )
                continue

            dc_v = get_datacite_payload(rec_v)
            if not dc_v:
                log_lines.append(
                    (
                        "no_datacite_payload",
                        concept_doi,
                        version_doi,
                        "Version record without datacite payload",
                    )
                )
                continue

            attrs_v = dc_v.get("attributes", dc_v)
            rels_v = attrs_v.get("relatedIdentifiers", []) or []

            # ověříme IsVersionOf -> concept DOI
            is_version_of = False
            for rel in rels_v:
                if (
                    rel.get("relationType") == "IsVersionOf"
                    and rel.get("relatedIdentifierType") == "DOI"
                ):
                    rid_norm = normalize_doi(rel.get("relatedIdentifier"))
                    if rid_norm == concept_doi:
                        is_version_of = True
                        break

            if is_version_of:
                zenodo_versions_to_drop.add(version_doi)
                log_lines.append(
                    (
                        "drop_version",
                        concept_doi,
                        version_doi,
                        "HasVersion + IsVersionOf match; dropping version DOI",
                    )
                )
            else:
                # necháme záznam být, ale zalogujeme ho jako potenciální neúplnou vazbu
                log_lines.append(
                    (
                        "inconsistent_isversionof",
                        concept_doi,
                        version_doi,
                        "HasVersion present, but IsVersionOf back-link missing",
                    )
                )

    # druhý průchod: zapisujeme jen ty záznamy, které NEJSOU ve versions_to_drop
    kept = []
    for rec in records:
        doi_norm = normalize_doi(rec.get("doi"))
        if doi_norm and doi_norm in zenodo_versions_to_drop:
            continue
        kept.append(rec)

    dump_jsonl(output_path, kept)

    if log_path:
        with log_path.open("w", encoding="utf-8") as f:
            f.write("status\tconcept_doi\tversion_doi\tnote\n")
            for status, cdoi, vdoi, note in log_lines:
                f.write(
                    f"{status}\t{cdoi or ''}\t{vdoi or ''}\t{note or ''}\n"
                )

    # shrnutí na stdout
    print(f"Input records: {len(records)}", file=sys.stderr)
    print(f"Detected Zenodo concept DOIs: {len(concept_dois)}", file=sys.stderr)
    print(f"Dropped Zenodo version DOIs: {len(zenodo_versions_to_drop)}", file=sys.stderr)
    print(f"Output records: {len(kept)}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(
        description="Collapse Zenodo version DOIs to concept DOIs (DataCite only)."
    )
    ap.add_argument(
        "--input",
        required=True,
        help="Input deduplicated JSONL (datasets_dedup.jsonl)",
    )
    ap.add_argument(
        "--output",
        required=True,
        help="Output JSONL with Zenodo versions removed",
    )
    ap.add_argument(
        "--log",
        help="Optional TSV log of decisions (status, concept_doi, version_doi, note)",
    )
    args = ap.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    log_path = Path(args.log) if args.log else None

    collapse_zenodo_versions(input_path, output_path, log_path)


if __name__ == "__main__":
    main()

