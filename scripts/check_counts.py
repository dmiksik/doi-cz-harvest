#!/usr/bin/env python3
import csv
import argparse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ANALYSIS_DIR = BASE_DIR / "data" / "analysis"
CONCEPTS_DIR = ANALYSIS_DIR / "zenodo_concepts"


def count_for_ror(csv_path: Path, ror: str, year_from: int | None, year_to: int | None):
    """
    Spočítá:
      - celkový počet datasetů pro daný ROR,
      - počet datasetů v daném rozsahu let (pokud je zadán).
    Vyhodnocuje sloupce: ror_ids, year.
    """
    total = 0
    in_range = 0

    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ror_ids_raw = (row.get("ror_ids") or "").strip()
            if not ror_ids_raw:
                continue
            ror_ids = [r.strip() for r in ror_ids_raw.split(";") if r.strip()]
            if ror not in ror_ids:
                continue

            total += 1

            if year_from is None and year_to is None:
                # rok neřešíme
                continue

            year_val = (row.get("year") or "").strip()
            if not year_val:
                continue
            try:
                y = int(year_val)
            except ValueError:
                continue

            if year_from is not None and y < year_from:
                continue
            if year_to is not None and y > year_to:
                continue

            in_range += 1

    return total, in_range


def main():
    ap = argparse.ArgumentParser(
        description="Základní kontrola počtů datasetů podle ROR (DOI vs. Zenodo–concepts)."
    )
    ap.add_argument("--ror", required=True, help="ROR ID, např. https://ror.org/024d6js02")
    ap.add_argument("--from-year", type=int, default=None, help="Rok od (včetně)")
    ap.add_argument("--to-year", type=int, default=None, help="Rok do (včetně)")
    args = ap.parse_args()

    doi_csv = ANALYSIS_DIR / "datasets_flat.csv"
    concepts_csv = CONCEPTS_DIR / "datasets_flat.csv"

    print(f"Kontrola pro ROR: {args.ror}")
    print(f"Rozsah let: {args.from_year} – {args.to_year}\n")

    doi_total, doi_in_range = count_for_ror(doi_csv, args.ror, args.from_year, args.to_year)
    print("DOI režim (všechny verze včetně Zenodo):")
    print(f"  Celkem datasetů: {doi_total}")
    if args.from_year is not None or args.to_year is not None:
        print(f"  V rozsahu let:  {doi_in_range}")
    print()

    concepts_total, concepts_in_range = count_for_ror(
        concepts_csv, args.ror, args.from_year, args.to_year
    )
    print("Zenodo – jen kanonické DOI:")
    print(f"  Celkem datasetů: {concepts_total}")
    if args.from_year is not None or args.to_year is not None:
        print(f"  V rozsahu let:  {concepts_in_range}")


if __name__ == "__main__":
    main()

