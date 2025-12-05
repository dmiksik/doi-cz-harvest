# DOI CZ Harvest

Scripts and a small FastAPI web app for harvesting, deduplicating and exploring DOIs of **datasets** produced by research organisations in the Czech Republic, using ROR-based affiliation matching.

---

## Repository layout

```text
.
├── app/               # FastAPI dashboard
├── data/
│   ├── analysis/      # aggregated outputs (incl. zenodo_concepts/)
│   ├── cz_datasets/   # harvested JSONL from DataCite & Crossref
│   ├── processed/     # deduplicated JSONL + summary + institution TSVs
│   └── raw/           # raw external data (e.g. ROR dump)
├── scripts/
│   ├── harvest_cz_dataset.py
│   ├── dedup_and_stats.py
│   ├── analyze_datasets.py
│   └── collapse_zenodo_versions.py
├── requirements.txt
└── README.md
````

**Key data files:**

* `data/cz_datasets/*.jsonl` – harvested records from DataCite / Crossref
* `data/processed/datasets_dedup.jsonl(.gz)` – deduplicated per DOI
* `data/processed/datasets_dedup_zenodo_concepts.jsonl` – Zenodo collapsed to concept DOI
* `data/processed/institutions_doi.tsv` – institution-level counts (DOI level)
* `data/processed/institutions_zenodo_concepts.tsv` – institution-level counts (Zenodo concept level)
* `data/analysis/*` and `data/analysis/zenodo_concepts/*` – flat tables for analysis / Excel

---

## Installation

```bash
git clone https://github.com/dmiksik/doi-cz-harvest.git
cd doi-cz-harvest

python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\Activate.ps1

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
* `data/processed/institutions.tsv` (legacy DOI-level institution summary, not used by the web UI)

---

## 3. Collapse Zenodo versions to concept DOI

Zenodo exposes separate DOIs for versions + one concept DOI.
For statistics on “how many datasets”, you can collapse Zenodo to concept level:

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

### Zenodo version DOIs level

```bash
python scripts/analyze_datasets.py \
  --dedup data/processed/datasets_dedup.jsonl \
  --out-dir data/analysis \
  --ror-dump data/raw/v1.74-2025-11-24-ror-data_schema_v2.json \
  --institutions-out data/processed/institutions_doi.tsv
```

### Zenodo concept DOI level

```bash
python scripts/analyze_datasets.py \
  --dedup data/processed/datasets_dedup_zenodo_concepts.jsonl \
  --out-dir data/analysis/zenodo_concepts \
  --ror-dump data/raw/v1.74-2025-11-24-ror-data_schema_v2.json \
  --institutions-out data/processed/institutions_zenodo_concepts.tsv
```

Main outputs:

* `data/analysis/timeline.tsv` and `data/analysis/zenodo_concepts/timeline.tsv` – per-year counts
* `orcid_coverage.json`, `orcid_by_institution.tsv` (both levels)
* `funders_*.tsv`, `licenses_datacite.tsv`, `license_dataset_summary.json`
* `datasets_flat.csv` (both levels) – one row per deduplicated DOI / concept DOI

### How datasets and authors are counted per institution

For both `institutions_doi.tsv` and `institutions_zenodo_concepts.tsv`:

* **Author identity**

  * If ORCID is present, authors are identified by normalised ORCID: `orcid:0000-0000-0000-0000`.
  * Otherwise, by normalised name: `name:family,given` (lowercased, trimmed).

* **Assigning a dataset to an institution (ROR)**

  * A dataset is counted for a given ROR **only if at least one author explicitly lists this ROR in their per-author affiliation** (DataCite or Crossref).
  * For a given ROR, each dataset is counted **at most once**, even if multiple authors have that ROR.

* **Counting authors per institution (`author_count`)**

  * For each ROR, we take the **set of unique authors** who have this ROR in their affiliation in any dataset.
  * An author is counted **once per institution**, even if they appear in many datasets.
  * The same person can be counted once in multiple institutions if they have multiple ROR affiliations.

---

## 5. Web dashboard

FastAPI app for interactive browsing:

```bash
uvicorn app.app:app --host 0.0.0.0 --port 8000
```

Then open:

* `http://localhost:8000/?mode=doi` – DOI mode (all DOIs, incl. all Zenodo versions)
* `http://localhost:8000/?mode=concepts` – Zenodo concept mode (Zenodo collapsed to concept DOIs)

UI:

* Left: institutions (with `dataset_count` / `author_count`)
* Right: per-institution list of datasets, with year filter
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
