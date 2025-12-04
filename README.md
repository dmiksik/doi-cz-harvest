# DOI CZ Harvest

Scripts and a small web app for harvesting, deduplicating and exploring DOIs of **datasets** produced by research organisations in the Czech Republic (via ROR-based affiliation matching).

---

## Repository layout

```text
.
├── app/                      # FastAPI dashboard
├── data/
│   ├── analysis/             # aggregated outputs (incl. zenodo_concepts/)
│   ├── cz_datasets/          # harvested JSONL from DataCite & Crossref
│   ├── processed/            # deduplicated JSONL + summary
│   └── raw/                  # raw external data (e.g. ROR dump)
├── scripts/
│   ├── harvest_cz_dataset.py
│   ├── dedup_and_stats.py
│   ├── analyze_datasets.py
│   └── collapse_zenodo_versions.py
├── requirements.txt
└── README.md
````

Key data files:

* `data/cz_datasets/*.jsonl` – harvested records from DataCite / Crossref
* `data/processed/datasets_dedup.jsonl(.gz)` – deduplicated per DOI
* `data/processed/datasets_dedup_zenodo_concepts.jsonl` – Zenodo collapsed to concept DOI
* `data/processed/institutions.tsv` – institution-level counts
* `data/analysis/*` + `data/analysis/zenodo_concepts/*` – flat tables for analysis / Excel

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

## 1. Harvest (CZ-affiliated datasets)

```bash
python scripts/harvest_cz_dataset.py \
  --ror-dump data/raw/v1.74-2025-11-24-ror-data_schema_v2.json \
  --out-dir data/cz_datasets
```

Outputs:

* `data/cz_datasets/datacite_cz_datasets.jsonl`
* `data/cz_datasets/crossref_cz_datasets.jsonl`

(One JSON object per line, incl. `source`, `doi`, ROR info and full metadata.)

---

## 2. Deduplicate & basic stats (DOI level)

```bash
python scripts/dedup_and_stats.py \
  --datacite data/cz_datasets/datacite_cz_datasets.jsonl \
  --crossref data/cz_datasets/crossref_cz_datasets.jsonl \
  --ror-dump data/raw/v1.74-2025-11-24-ror-data_schema_v2.json \
  --out-dir data/processed
```

Outputs:

* `data/processed/datasets_dedup.jsonl` (+ optional `.gz`)
* `data/processed/summary_stats.json`
* `data/processed/institutions.tsv`

In `institutions.tsv`:

* `dataset_count` – number of deduplicated DOIs linked to the ROR
* `author_count` – number of distinct authors who **explicitly** list this ROR in their affiliation

---

## 3. Collapse Zenodo versions to concept DOI

Zenodo exposes separate DOIs for versions + one concept DOI. For statistics on “how many datasets”, you can collapse Zenodo to concept level:

```bash
python scripts/collapse_zenodo_versions.py \
  --dedup data/processed/datasets_dedup.jsonl \
  --out-dedup data/processed/datasets_dedup_zenodo_concepts.jsonl \
  --log data/analysis/zenodo_versions_log.tsv \
  --concept-analysis-dir data/analysis/zenodo_concepts
```

Outputs mirror the DOI-level analysis (flat tables, ORCID coverage, funders, licences), but with Zenodo reduced to concept DOIs.

---

## 4. Analysis tables

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

After running `collapse_zenodo_versions.py`, the same set exists under `data/analysis/zenodo_concepts/`.

---

## 5. Web dashboard

FastAPI app for interactive browsing:

```bash
uvicorn app.app:app --host 0.0.0.0 --port 8000
```

Then open:

* `http://localhost:8000/` – dashboard

  * left: institutions (with dataset / author counts)
  * right: per-institution list of datasets with year filter
  * toggle between:

    * **DOI (all versions incl. Zenodo)**
    * **Zenodo – canonical DOI only (concept DOIs)**
* `http://localhost:8000/docs` – API docs

For server deployment, run uvicorn behind a reverse proxy (e.g. Nginx).

---

## Notes

All counts depend on the quality of:

* ROR usage in metadata (dataset- and author-level),
* ORCID coverage,
* licence declarations.

The goal is a **transparent, reproducible pipeline** for analysing CZ dataset DOIs, not a polished production service.

## License

TBD.
