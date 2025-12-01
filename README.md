Tady máš hotový návrh `README.md`, který můžeš jen zkopírovat do repa a případně lehce doladit názvy souborů, pokud se něco jmenuje jinak.

````markdown
# DOI CZ Harvest

Scripts and a small web app for harvesting, deduplicating, and exploring DOIs of **datasets** produced by research organisations in the Czech Republic.

The workflow:

1. **Harvest** dataset DOIs from:
   - [DataCite](https://api.datacite.org/)
   - [Crossref](https://api.crossref.org/)
   using affiliation to **CZ organisations** identified via [ROR](https://ror.org/).
2. **Deduplicate** DOIs across sources and **merge** metadata.
3. **Compute statistics** per:
   - source (DataCite / Crossref),
   - ROR institution,
   - ORCID coverage,
   - funders,
   - licences.
4. Serve a simple **FastAPI dashboard** for interactive exploration (by institution, year, etc.).

The repo is intended as a reproducible pipeline and a demonstrator for the CZ data landscape, not as a fully polished product.

---

## Repository structure

Recommended layout (what this repo is designed for):

```text
doi-cz-harvest/
├─ scripts/
│  ├─ harvest_datacite.py      # harvest from DataCite API (datasets with CZ RORs)
│  ├─ harvest_crossref.py      # harvest from Crossref API
│  ├─ dedup_and_stats.py       # deduplication + basic stats + institutions.tsv
│  └─ analyze_datasets.py      # deeper analysis + timeline, ORCID coverage, licences, CSV
├─ app/
│  └─ app.py                   # FastAPI app (simple dashboard)
├─ data/
│  ├─ raw/                     # (optional) raw exports from APIs – NOT committed
│  ├─ processed/               # deduplicated + summarised data
│  │  ├─ datasets_dedup.jsonl
│  │  ├─ institutions.tsv
│  │  └─ summary_stats.json
│  └─ analysis/                # aggregated tables for plots / Excel
│     ├─ timeline.tsv
│     ├─ orcid_coverage.json
│     ├─ license_dataset_summary.json
│     └─ datasets_flat.csv
├─ requirements.txt
└─ README.md
````

You can adjust the exact paths as you like, but the scripts assume something close to this.

---

## Data model (high-level)

* **Unit of analysis:** one row per **dataset DOI** (deduplicated across DataCite / Crossref).
* **Affiliation to CZ institutions:** via **ROR IDs** in harvested records.
* **Person identifiers:**

  * ORCID preferred (`orcid:0000-0002-...`),
  * otherwise `name:family,given` (lowercased).

### `processed/datasets_dedup.jsonl`

Each line:

```json
{
  "doi": "10.xxxx/abcd",
  "sources": ["datacite", "crossref"],
  "ror_ids": ["https://ror.org/0xxxxx", "https://ror.org/0yyyyy"],
  "records": {
    "datacite": { ... full DataCite record ... },
    "crossref": { ... full Crossref record ... }
  }
}
```

* `sources`: which registries have metadata for this DOI.
* `ror_ids`: all CZ ROR IDs this DOI was harvested under (dataset-level affiliation).

### `processed/summary_stats.json`

Example:

```json
{
  "raw_counts": {
    "datacite": 9777,
    "crossref": 2
  },
  "unique_doi": 7097,
  "overlap_doi": 0,
  "institution_count": 184
}
```

* `raw_counts` – number of harvested records before deduplication (per source).
* `unique_doi` – number of distinct dataset DOIs after deduplication.
* `overlap_doi` – DOIs present in **both** DataCite and Crossref.
* `institution_count` – number of CZ ROR institutions with at least one dataset.

### `processed/institutions.tsv`

Tab-separated file with header:

```text
ror_id  name  dataset_count  author_count
```

Where:

* `ror_id` – full ROR URI, e.g. `https://ror.org/0xxxxx`.
* `name` – human-readable name from ROR dump.
* `dataset_count` – number of **deduplicated dataset DOIs** that have this ROR among their affiliations.
* `author_count` – number of **distinct authors who explicitly list this ROR in their own affiliation**
  (this is important – see below).

> **Important:**
> `author_count` **does NOT** mean “all authors of those datasets”.
> It only counts authors who have this ROR in their **author-level affiliation**.

Concretely:

* For each dataset DOI, we examine authors in DataCite and Crossref metadata.
* For each author:

  * we create a person key (ORCID if available, otherwise normalised name),
  * we scan their `affiliation` entries for ROR IDs.
* For each `(author, ROR)` pair we record “author X has affiliation ROR Y”.
* `author_count` for a given institution is the size of the set of persons who have that ROR in their affiliation for at least one dataset.

So:

* if a dataset is shared between MU and VUT, and a given person lists **only MU** in their affiliation, they count for MU but **not** for VUT.
* if an author lists **both MU and VUT**, they are counted for both institutions.

---

## `analysis/` outputs

### `analysis/timeline.tsv`

Tab-separated file:

```text
year  total  datacite  crossref
2020  1234   1230      10
2021  1500   1495      8
...
```

* `year` – publication year of the dataset (from:

  * DataCite `attributes.publicationYear`, or
  * Crossref `issued.date-parts[0][0]`).
* `total` – number of **unique DOIs** for this year.
* `datacite` – DOIs for this year with DataCite metadata.
* `crossref` – DOIs for this year with Crossref metadata.

A DOI present in both sources is counted:

* once in `total`,
* once in `datacite`,
* once in `crossref`.

### `analysis/orcid_coverage.json`

Global ORCID coverage numbers (across all deduplicated DOIs):

```json
{
  "datasets_total": ...,
  "datasets_with_at_least_one_orcid": ...,
  "datasets_with_at_least_one_orcid_pct": ...,
  "persons_total": ...,
  "persons_with_orcid": ...,
  "persons_with_orcid_pct": ...
}
```

* dataset-level: how many datasets have at least one author with ORCID,
* person-level: how many distinct persons have ORCID.

### `analysis/license_dataset_summary.json`

Rough open licence profile per dataset:

```json
{
  "open":  ...,   // dataset has at least one licence recognised as "open"
  "nonopen": ..., // dataset has licence(s), but none matched as "open"
  "none":  ...    // dataset has no licence metadata
}
```

Heuristics for “open” look for Creative Commons, CC0, ODC-PDDL, ODbL, etc., in
`rightsUri` / `rightsIdentifier` / `rights`.

### `analysis/datasets_flat.csv`

One row per deduplicated DOI, ready for Excel / further analysis. Columns include:

* DOI, sources (DataCite / Crossref),
* ROR IDs (semicolon-separated),
* year,
* DataCite client ID / publisher / resourceTypeGeneral / title / licences,
* Crossref member / publisher / year / title,
* `n_authors_total`, `n_authors_with_orcid`.

---

## Requirements

* Python 3.10+ (3.11/3.12 are fine)
* `pip` for installing dependencies

Python dependencies (see `requirements.txt`):

* `requests` (for API calls in harvester scripts),
* `fastapi`, `uvicorn` (for the web app),
* `pandas` / `pyarrow` / `duckdb` (optional, if you extend analysis; the core scripts as given only use stdlib + FastAPI).

---

## Setup

Clone the repository:

```bash
git clone https://github.com/dmiksik/doi-cz-harvest.git
cd doi-cz-harvest
```

Create and activate virtualenv:

```bash
python -m venv venv
source venv/bin/activate        # Windows PowerShell: venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

---

## 1. Harvesting from DataCite & Crossref

The harvest scripts are in `scripts/`. They query the APIs in windows (date / updated ranges, ROR filters, etc.) and produce JSONL.

Usage patterns (details in `-h` of each script):

```bash
# DataCite harvest – example
python scripts/harvest_datacite.py \
  --ror-list data/raw/cz_ror_ids.txt \
  --out data/raw/datacite_cz_datasets.jsonl

# Crossref harvest – example
python scripts/harvest_crossref.py \
  --ror-list data/raw/cz_ror_ids.txt \
  --out data/raw/crossref_cz_datasets.jsonl
```

Inputs:

* `cz_ror_ids.txt` – one CZ ROR ID per line, e.g. `https://ror.org/0xxxxx`.

Outputs:

* `data/raw/datacite_cz_datasets.jsonl`
* `data/raw/crossref_cz_datasets.jsonl`

Each line is a small JSON object containing at least:

* `source`: `"datacite"` / `"crossref"`,
* `doi`: original DOI,
* `ror_id`: ROR used in that particular API query (RHS of the affiliation),
* `record`: the full JSON from the registry.

---

## 2. Deduplication & basic stats

Script: `scripts/dedup_and_stats.py`.

It:

1. Reads harvested JSONL files from DataCite and Crossref.
2. Normalises DOIs.
3. Groups records by DOI, merges:

   * `sources` set,
   * `ror_ids` set,
   * full metadata under `records.datacite` / `records.crossref`.
4. Counts per-institution datasets and authors with explicit ROR in affiliation.

Usage:

```bash
python scripts/dedup_and_stats.py \
  --datacite data/raw/datacite_cz_datasets.jsonl \
  --crossref data/raw/crossref_cz_datasets.jsonl \
  --ror-dump data/raw/ror_dump.json \
  --out-dir data/processed
```

Where:

* `--datacite` – harvested DataCite JSONL file,
* `--crossref` – harvested Crossref JSONL file,
* `--ror-dump` – full ROR JSON dump (used to resolve CZ institutions and names),
* `--out-dir` – output directory (e.g. `data/processed`).

Outputs:

* `data/processed/datasets_dedup.jsonl`
* `data/processed/summary_stats.json`
* `data/processed/institutions.tsv`

---

## 3. Analysis & CSV export

Script: `scripts/analyze_datasets.py`.

It reads `datasets_dedup.jsonl` (and optionally ROR dump) and builds ready-to-use aggregates:

```bash
python scripts/analyze_datasets.py \
  --dedup data/processed/datasets_dedup.jsonl \
  --out-dir data/analysis \
  --ror-dump data/raw/ror_dump.json
```

Outputs (in `data/analysis`):

* `timeline.tsv` – per-year counts,
* `repos_datacite.tsv`, `repos_crossref.tsv` – repos / publishers overview,
* `orcid_coverage.json`, `orcid_by_institution.tsv`,
* `funders_datacite.tsv`, `funders_crossref.tsv`,
* `licenses_datacite.tsv`, `license_dataset_summary.json`,
* `datasets_flat.csv` – full flattened dataset table.

---

## 4. Web dashboard (FastAPI app)

The simplest way to inspect the data is via the FastAPI app in `app/app.py`.
It:

* loads `processed/summary_stats.json`, `processed/institutions.tsv`, `processed/datasets_dedup.jsonl`,
* loads selected `analysis/` outputs (timeline, ORCID, licences),
* exposes JSON API under `/api/...`,
* serves a very simple HTML/JS dashboard at `/`.

Run locally (from repo root, with venv active):

```bash
uvicorn app.app:app --reload --host 0.0.0.0 --port 8000
```

Then open in browser:

* `http://localhost:8000/` – dashboard
* `http://localhost:8000/docs` – auto-generated API docs

To deploy on a server behind Nginx, run uvicorn with:

```bash
uvicorn app.app:app --host 127.0.0.1 --port 8001
```

…and put a simple reverse proxy in front. See comments in `app/app.py` for details.

---

## Notes & limitations

* The quality of per-author affiliations (and thus `author_count`) depends heavily on:

  * how systematically repositories / publishers fill `affiliation`,
  * whether they include **ROR IDs** at author level.
* No attempt is made to disambiguate people beyond ORCID + name normalisation.
* “Open licence” detection is heuristic and conservative.
* This repo focuses on the **post-2020** landscape, but scripts work for any year range you decide to harvest.

---

## License

TBD – choose what you prefer (e.g. MIT/Apache-2.0) and update this section.

---

## Acknowledgements

This work builds on metadata and services provided by:

* [DataCite](https://datacite.org/)
* [Crossref](https://www.crossref.org/)
* [ROR](https://ror.org/)

and is intended to support CZ research data infrastructure and analysis.

```

Když mi pošleš aktuální reálnou strukturu repa (co přesně v `scripts/` a `data/` máš), můžeme to jemně doladit, aby README přesně sedělo na stav tvého projektu.
::contentReference[oaicite:0]{index=0}
```

