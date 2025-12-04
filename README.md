# DOI CZ Harvest

Scripts and a small web app for harvesting, deduplicating, and exploring DOIs of **datasets** produced by research organisations in the Czech Republic (via ROR-based affiliation matching).

---

## Repository layout

```text
.
├── app/
│   └── app.py                      # FastAPI dashboard
├── data/
│   ├── analysis/                   # aggregated outputs (for Excel/plots)
│   ├── cz_datasets/                # harvested JSONL from DataCite & Crossref
│   ├── processed/                  # deduplicated + summarised data
│   └── raw/                        # raw external data (e.g. ROR dumps)
├── scripts/
│   ├── harvest_cz_dataset.py       # harvesting
│   ├── dedup_and_stats.py          # dedup + summary + institutions.tsv
│   └── analyze_datasets.py         # deeper analysis + CSV/TSV outputs
├── requirements.txt
└── README.md
````

Key data files:

* `data/cz_datasets/*.jsonl` – harvested dataset records from DataCite / Crossref
* `data/processed/datasets_dedup.jsonl.gz` – deduplicated datasets (per DOI)
* `data/processed/institutions.tsv` – per-institution counts
* `data/analysis/*` – ready-to-use tables for visualisation / Excel

---

## Installation

```bash
git clone https://github.com/dmiksik/doi-cz-harvest.git
cd doi-cz-harvest

python -m venv venv
source venv/bin/activate         # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

---

## 1. Harvest

Harvest datasets with CZ affiliations from DataCite & Crossref:

```bash
python harvest_cz_dataset.py \
  --data/raw/ror-dump v1.74-2025-11-24-ror-data_schema_v2.json \
  --out-dir ./cz_datasets
```

Typical outputs:

* `data/cz_datasets/datacite_cz_datasets.jsonl`
* `data/cz_datasets/crossref_cz_datasets.jsonl`

(Each line = one record, including `source`, `doi`, `ror_id`, full `record` JSON.)

---

## 2. Deduplicate & basic stats

Merge DataCite / Crossref records per DOI, compute basic stats and institutional counts:

```bash
python scripts/dedup_and_stats.py \
  --datacite data/cz_datasets/datacite_cz_datasets.jsonl \
  --crossref data/cz_datasets/crossref_cz_datasets.jsonl \
  --ror-dump data/raw/v1.74-2025-11-24-ror-data_schema_v2.json \
  --out-dir data/processed
```

Outputs:

* `datasets_dedup.jsonl` (often stored as `datasets_dedup.jsonl.gz`)
* `summary_stats.json`
* `institutions.tsv`

In `institutions.tsv`:

* `dataset_count` = number of deduplicated DOIs with that ROR
* `author_count` = number of distinct authors who **explicitly list this ROR in their affiliation** (author-level ROR, not just dataset-level)

---

## 3. Analysis

Produce convenient tables for further analysis (timeline, ORCID coverage, funders, licences, flat CSV):

```bash
python scripts/analyze_datasets.py \
  --dedup data/processed/datasets_dedup.jsonl \
  --out-dir data/analysis \
  --ror-dump data/raw/v1.74-2025-11-24-ror-data_schema_v2.json
```

Main outputs:

* `timeline.tsv` – per-year DOI counts
* `orcid_coverage.json`, `orcid_by_institution.tsv`
* `funders_*.tsv`, `licenses_datacite.tsv`, `license_dataset_summary.json`
* `datasets_flat.csv` – one row per deduplicated DOI

---

## 4. Web dashboard

FastAPI app for interactive browsing of the results:

```bash
uvicorn app.app:app --reload --host 0.0.0.0 --port 8000
```

Then open:

* `http://localhost:8000/` – dashboard (institutions, datasets, year filter)
* `http://localhost:8000/docs` – API docs

For server deployment, run uvicorn behind a reverse proxy (e.g. Nginx).

---

## Notes

* All counts and coverage depend heavily on the quality of:

  * ROR usage in metadata (dataset-level and author-level),
  * ORCID coverage,
  * licence declarations.
* The goal is to provide a **transparent, reproducible pipeline** for analysing CZ dataset DOIs, not a polished product.

## License

TBD.

