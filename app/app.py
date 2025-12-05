from pathlib import Path
from collections import defaultdict
import csv
import json
from urllib.parse import unquote

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse

# Cesty vzhledem ke kořeni repozitáře
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PROCESSED_DIR = DATA_DIR / "processed"
ANALYSIS_DIR = DATA_DIR / "analysis"
CONCEPTS_ANALYSIS_DIR = ANALYSIS_DIR / "zenodo_concepts"

# Režimy: DOI-level vs. Zenodo concept-level
MODE_CONFIGS = {
    "doi": {
        "label": "DOI (všechny verze včetně Zenodo)",
        "dedup_path": PROCESSED_DIR / "datasets_dedup.jsonl",
        "inst_tsv": PROCESSED_DIR / "institutions_doi.tsv",
        "orcid_path": ANALYSIS_DIR / "orcid_coverage.json",
        "license_path": ANALYSIS_DIR / "license_dataset_summary.json",
        "summary_path": PROCESSED_DIR / "summary_stats.json",
        "flat_path": ANALYSIS_DIR / "datasets_flat.csv",
    },
    "concepts": {
        "label": "Zenodo – jen kanonické DOI",
        "dedup_path": PROCESSED_DIR / "datasets_dedup_zenodo_concepts.jsonl",
        "inst_tsv": PROCESSED_DIR / "institutions_zenodo_concepts.tsv",
        "orcid_path": CONCEPTS_ANALYSIS_DIR / "orcid_coverage.json",
        "license_path": CONCEPTS_ANALYSIS_DIR / "license_dataset_summary.json",
        "summary_path": None,  # dopočítáme z DOI summary + concept dat
        "flat_path": CONCEPTS_ANALYSIS_DIR / "datasets_flat.csv",
    },
}

# Sem si při startu nahrneme všechna data
DATA: dict[str, dict] = {}


def load_institutions_from_table(path: Path):
    """
    Čte TSV (institutions.tsv nebo orcid_by_institution.tsv) a vrací seznam institucí
    ve formátu {ror_id, name, dataset_count, author_count}.
    Snaží se být tolerantní k názvům sloupců (dataset_count vs datasets_total, persons_total atd.).
    """
    institutions = []
    if not path.exists():
        raise RuntimeError(f"Institutions file not found: {path}")

    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fieldnames = reader.fieldnames or []

        def pick_key(candidates, row_keys):
            """
            Najdi první klíč, který obsahuje některý z patterns.
            Preferuje ty, kde je zároveň 'total' nebo 'count'.
            """
            scored = []
            for k in row_keys:
                lk = k.lower()
                if any(p in lk for p in candidates):
                    score = 0
                    if "total" in lk or "count" in lk:
                        score -= 1
                    scored.append((score, k))
            if not scored:
                return None
            scored.sort()
            return scored[0][1]

        dataset_key = None
        person_key = None
        if fieldnames:
            dataset_key = pick_key(["dataset"], fieldnames)
            person_key = pick_key(["person", "author"], fieldnames)

        for row in reader:
            # ROR
            ror_id = (
                row.get("ror_id")
                or row.get("ror")
                or row.get("affiliationIdentifier")
                or row.get("institution_ror")
            )
            if not ror_id:
                continue

            # jméno instituce
            name = (
                row.get("name")
                or row.get("institution_name")
                or row.get("label")
                or ""
            )

            # dataset_count
            ds_count = 0
            if dataset_key and dataset_key in row:
                try:
                    ds_count = int(row[dataset_key] or 0)
                except Exception:
                    ds_count = 0
            else:
                # explicitní fallbacky, kdyby heuristika selhala
                for alt in ("datasets_total", "dataset_count", "datasets"):
                    if alt in row:
                        try:
                            ds_count = int(row[alt] or 0)
                        except Exception:
                            ds_count = 0
                        break

            # author_count
            author_count = 0
            if person_key and person_key in row:
                try:
                    author_count = int(row[person_key] or 0)
                except Exception:
                    author_count = 0
            else:
                for alt in ("persons_total", "author_count", "persons", "authors"):
                    if alt in row:
                        try:
                            author_count = int(row[alt] or 0)
                        except Exception:
                            author_count = 0
                        break

            institutions.append(
                {
                    "ror_id": ror_id,
                    "name": name,
                    "dataset_count": ds_count,  # přepíšeme podle datasets_flat
                    "author_count": author_count,
                }
            )

    # filtr dataset_count > 0 uděláme až po spojení s datasets_flat
    return institutions


def build_datasets_by_institution_from_flat(flat_path: Path):
    """
    Čte datasets_flat.csv (nebo zenodo_concepts/datasets_flat.csv)
    a vrací:
      - mapu ROR -> seznam datasetů (pro pravý panel),
      - počet unikátních DOI (pro summary).

    Sloupce detekuje takto (priorita):
      - doi: 'doi'
      - title: 'datacite_title' → 'title' → 'crossref_title'
      - year: 'year' → 'publicationyear' → 'datacite_year' → 'crossref_year'
      - ror: 'ror_ids' → 'ror' → 'ror_id'
      - sources: 'sources' → 'source' → 'provider' → 'origin'
    """
    if not flat_path.exists():
        raise RuntimeError(f"datasets_flat not found: {flat_path}")

    by_inst: dict[str, list] = defaultdict(list)
    dois_seen: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()

    with flat_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        lower_map = {fn.lower(): fn for fn in fieldnames}

        def find_exact(preferred: list[str]):
            for name in preferred:
                ln = name.lower()
                if ln in lower_map:
                    return lower_map[ln]
            return None

        doi_col = find_exact(["doi"])
        title_col = find_exact(["datacite_title", "title", "crossref_title"])
        year_col = find_exact(
            ["year", "publicationyear", "datacite_year", "datacite_publicationyear", "crossref_year"]
        )
        ror_col = find_exact(["ror_ids", "ror", "ror_id"])
        source_col = find_exact(["sources", "source", "provider", "origin"])

        for row in reader:
            # DOI
            doi = (row.get(doi_col) or "").strip() if doi_col else ""
            if doi:
                dois_seen.add(doi)

            # Title
            title = (row.get(title_col) or "").strip() if title_col else ""

            # Year
            year_val = (row.get(year_col) or "").strip() if year_col else ""
            year: int | str | None = year_val or None
            if year_val:
                try:
                    year = int(year_val)
                except Exception:
                    year = year_val  # necháme string

            # Sources
            sources: list[str] = []
            if source_col:
                sraw = (row.get(source_col) or "").strip()
                if sraw:
                    if ";" in sraw:
                        sources = [s.strip() for s in sraw.split(";") if s.strip()]
                    elif "," in sraw:
                        sources = [s.strip() for s in sraw.split(",") if s.strip()]
                    else:
                        sources = [sraw]

            # ROR – ror_ids může obsahovat víc hodnot oddělených středníkem
            rors_raw = (row.get(ror_col) or "").strip() if ror_col else ""
            if not rors_raw:
                continue
            ror_list = [r.strip() for r in rors_raw.split(";") if r.strip()]
            if not ror_list:
                continue

            ds = {
                "doi": doi,
                "title": title,
                "year": year,
                "sources": sources,
            }

            # dataset přiřadíme ke všem RORům z ror_ids;
            # duplicitní kombinace (ROR, DOI) nepočítáme víckrát
            for ror in ror_list:
                pair = (ror, doi or "")
                if doi and pair in seen_pairs:
                    continue
                if doi:
                    seen_pairs.add(pair)
                by_inst[ror].append(ds)

    return by_inst, len(dois_seen)


def load_data():
    """
    Načte data pro oba režimy (doi / concepts) do globálního slovníku DATA.
    """
    global DATA

    # DOI summary pro DOI režim
    doi_summary_path = MODE_CONFIGS["doi"]["summary_path"]
    if not doi_summary_path or not doi_summary_path.exists():
        raise RuntimeError(f"DOI summary_stats.json not found: {doi_summary_path}")
    with doi_summary_path.open(encoding="utf-8") as f:
        doi_summary = json.load(f)

    for mode, cfg in MODE_CONFIGS.items():
        # 1) seznam institucí z TSV
        inst_list = load_institutions_from_table(cfg["inst_tsv"])

        # 2) datasety podle instituce z datasets_flat*.csv
        datasets_by_inst, unique_doi_count = build_datasets_by_institution_from_flat(
            cfg["flat_path"]
        )

        # 3) ORCID coverage a licence pro daný režim
        with cfg["orcid_path"].open(encoding="utf-8") as f:
            orcid_cov = json.load(f)
        with cfg["license_path"].open(encoding="utf-8") as f:
            license_summary = json.load(f)

        # 4) summary
        if mode == "doi":
            summary = doi_summary
        else:
            summary = dict(doi_summary)
            summary["unique_doi"] = unique_doi_count
            summary["institution_count"] = len(inst_list)

        # 5) dataset_count ve sloupci „Instituce“ přepočítáme z datasets_by_inst
        for inst in inst_list:
            ror = inst["ror_id"]
            inst["dataset_count"] = len(datasets_by_inst.get(ror, []))

        # odfiltrujeme instituce bez datasetů
        inst_list = [i for i in inst_list if i["dataset_count"] > 0]
        inst_list.sort(key=lambda i: i["dataset_count"], reverse=True)

        DATA[mode] = {
            "label": cfg["label"],
            "summary": summary,
            "institutions": inst_list,
            "datasets_by_inst": datasets_by_inst,
            "orcid": orcid_cov,
            "licenses": license_summary,
        }


def resolve_mode(mode: str | None) -> str:
    if not mode or mode not in DATA:
        return "doi"
    return mode


HTML_PAGE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>CZ datasets (DataCite + Crossref)</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; }
    table { border-collapse: collapse; width: 100%; font-size: 14px; }
    th, td { border: 1px solid #ddd; padding: 0.3rem 0.5rem; }
    th { cursor: pointer; background: #f5f5f5; }
    tr:hover { background: #f0f8ff; }
    #layout { display: grid; grid-template-columns: 2fr 3fr; gap: 2rem; }
    #summary-box { margin-bottom: 1rem; padding: 0.5rem 0.75rem; background: #f5f5f5; border-radius: 6px; }
    h1 { margin-top: 0; }
    #mode-toggle { margin-bottom: 0.5rem; font-size: 14px; }
    #mode-toggle label { margin-right: 1rem; }
  </style>
</head>
<body>
  <h1>CZ datasets (DataCite + Crossref)</h1>

  <div id="mode-toggle">
    Zobrazení:
    <label>
      <input type="radio" name="mode" value="doi" checked>
      DOI (všechny verze včetně Zenodo)
    </label>
    <label>
      <input type="radio" name="mode" value="concepts">
      Zenodo – jen kanonické DOI
    </label>
  </div>

  <div id="summary-box">
    <div id="summary"></div>
    <div id="orcid"></div>
    <div id="licenses"></div>
  </div>

  <div id="layout">
    <div>
      <h2>Instituce</h2>
      <table id="inst-table">
        <thead>
          <tr><th>Instituce</th><th>Datasety</th><th>Autoři</th></tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
    <div>
      <h2 id="inst-title">Detail instituce</h2>
      <div id="year-filter" style="margin-bottom: 0.5rem; font-size: 14px;">
        <label>
          Rok od
          <input id="year-from" type="number" value="2020" style="width: 5rem; margin-right: 0.5rem;">
        </label>
        <label>
          do
          <input id="year-to" type="number" value="2025" style="width: 5rem; margin-right: 0.5rem;">
        </label>
        <button type="button" id="year-filter-apply">Filtrovat</button>
      </div>
      <table id="ds-table">
        <thead>
          <tr><th>DOI</th><th>Název</th><th>Rok</th><th>Zdroj</th></tr>
        </thead>
        <tbody></tbody>
      </table>
    </div>
  </div>

<script>
let currentMode = 'doi';
let currentInstitutionId = null;
let currentInstitutionName = null;
let currentInstitutionDatasets = [];

function apiUrl(path) {
  const sep = path.includes('?') ? '&' : '?';
  return path + sep + 'mode=' + encodeURIComponent(currentMode);
}

async function loadSummary() {
  const [summaryRes, orcidRes, licenseRes] = await Promise.all([
    fetch(apiUrl('/api/summary')),
    fetch(apiUrl('/api/orcid-coverage')),
    fetch(apiUrl('/api/licenses-summary'))
  ]);

  if (!summaryRes.ok) {
    console.error('Chyba při načítání summary:', summaryRes.status, await summaryRes.text());
    return;
  }
  if (!orcidRes.ok) {
    console.error('Chyba při načítání ORCID coverage:', orcidRes.status, await orcidRes.text());
    return;
  }
  if (!licenseRes.ok) {
    console.error('Chyba při načítání licencí:', licenseRes.status, await licenseRes.text());
    return;
  }

  const summary = await summaryRes.json();
  const orcid = await orcidRes.json();
  const licenses = await licenseRes.json();

  document.getElementById('summary').textContent =
    `DataCite (raw): ${summary.raw_counts.datacite}, `
    + `Crossref (raw): ${summary.raw_counts.crossref}, `
    + `unikátních DOI: ${summary.unique_doi}, `
    + `průnik (oba zdroje): ${summary.overlap_doi}, `
    + `institucí s daty: ${summary.institution_count}`;

  document.getElementById('orcid').textContent =
    `ORCID coverage – datasetů s aspoň jedním ORCID: `
    + `${orcid.datasets_with_at_least_one_orcid} / ${orcid.datasets_total} `
    + `(${orcid.datasets_with_at_least_one_orcid_pct.toFixed(1)} %), `
    + `osob s ORCID: ${orcid.persons_with_orcid} / ${orcid.persons_total} `
    + `(${orcid.persons_with_orcid_pct.toFixed(1)} %).`;

  document.getElementById('licenses').textContent =
    `Licence – otevřené: ${licenses.open}, `
    + `ne-otevřené: ${licenses.nonopen}, `
    + `bez zadané licence: ${licenses.none}.`;
}

async function loadInstitutions() {
  const res = await fetch(apiUrl('/api/institutions'));
  if (!res.ok) {
    console.error('Chyba při načítání institucí:', res.status, await res.text());
    alert('Nepodařilo se načíst seznam institucí. Podívej se do konzole pro detaily.');
    return;
  }
  const insts = await res.json();
  const tbody = document.querySelector('#inst-table tbody');
  tbody.innerHTML = '';
  insts.forEach(inst => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${inst.name}</td><td>${inst.dataset_count}</td><td>${inst.author_count}</td>`;
    tr.style.cursor = 'pointer';
    tr.addEventListener('click', () => loadInstitutionDetail(inst.ror_id, inst.name));
    tbody.appendChild(tr);
  });
}

function getYearFilter() {
  const fromInput = document.getElementById('year-from');
  const toInput = document.getElementById('year-to');
  let from = parseInt(fromInput.value, 10);
  let to = parseInt(toInput.value, 10);
  if (isNaN(from)) from = null;
  if (isNaN(to)) to = null;
  return { from, to };
}

function renderInstitutionDatasets() {
  const tbody = document.querySelector('#ds-table tbody');
  tbody.innerHTML = '';

  if (!currentInstitutionDatasets || currentInstitutionDatasets.length === 0) {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td colspan="4"><em>Žádné datasety pro tuto instituci (nebo se je nepodařilo dohledat).</em></td>';
    tbody.appendChild(tr);
    return;
  }

  const { from, to } = getYearFilter();

  let rows = currentInstitutionDatasets;

  // filtr aplikujeme jen pokud je nějaká hranice zadaná
  if (from !== null || to !== null) {
    rows = currentInstitutionDatasets.filter(ds => {
      if (ds.year === null || ds.year === undefined || ds.year === '') {
        return false; // bez roku do filtrovaného výpisu nebereme
      }
      const y = Number(ds.year);
      if (isNaN(y)) return false;
      if (from !== null && y < from) return false;
      if (to !== null && y > to) return false;
      return true;
    });
  }

  if (rows.length === 0) {
    const tr = document.createElement('tr');
    tr.innerHTML = '<td colspan="4"><em>Pro daný rozsah let nejsou žádné datasety.</em></td>';
    tbody.appendChild(tr);
    return;
  }

  rows.forEach(ds => {
    const tr = document.createElement('tr');
    const sources = (ds.sources || []).join(', ');
    const doiLink = ds.doi
      ? `<a href="https://doi.org/${ds.doi}" target="_blank" rel="noopener">${ds.doi}</a>`
      : '';
    tr.innerHTML = `
      <td>${doiLink}</td>
      <td>${ds.title || ''}</td>
      <td>${ds.year || ''}</td>
      <td>${sources}</td>`;
    tbody.appendChild(tr);
  });
}

function applyYearFilter() {
  // prostě znovu vykreslit aktuální instituci s aktuálními hodnotami filtrů
  renderInstitutionDatasets();
}

async function loadInstitutionDetail(ror_id, name) {
  try {
    const url = apiUrl('/api/institutions/' + encodeURIComponent(ror_id));
    const res = await fetch(url);
    if (!res.ok) {
      const text = await res.text();
      console.error('Chyba při načítání detailu instituce:', res.status, text);
      alert('Nepodařilo se načíst detail instituce (HTTP ' + res.status + '). Podívej se do konzole.');
      return;
    }
    const data = await res.json();

    currentInstitutionId = ror_id;
    currentInstitutionName = name;
    currentInstitutionDatasets = data.datasets || [];

    document.getElementById('inst-title').textContent = `Detail instituce: ${name}`;

    renderInstitutionDatasets();
  } catch (err) {
    console.error('loadInstitutionDetail error:', err);
    alert('Nastala chyba při načítání detailu instituce. Podívej se do konzole.');
  }
}

function setMode(mode) {
  if (mode === currentMode) return;
  currentMode = mode;

  // reset detailu
  currentInstitutionId = null;
  currentInstitutionName = null;
  currentInstitutionDatasets = [];
  document.getElementById('inst-title').textContent = 'Detail instituce';
  document.querySelector('#ds-table tbody').innerHTML = '';

  // znovu načíst summary + seznam institucí
  loadSummary();
  loadInstitutions();
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('input[name="mode"]').forEach(el => {
    el.addEventListener('change', (e) => {
      if (e.target.checked) {
        setMode(e.target.value);
      }
    });
  });

  const filterBtn = document.getElementById('year-filter-apply');
  if (filterBtn) {
    filterBtn.addEventListener('click', () => {
      applyYearFilter();
    });
  }

  loadSummary();
  loadInstitutions();
});
</script>
</body>
</html>
"""

app = FastAPI()


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML_PAGE


@app.get("/api/summary", response_class=JSONResponse)
def api_summary(mode: str = Query("doi")):
    m = resolve_mode(mode)
    return DATA[m]["summary"]


@app.get("/api/orcid-coverage", response_class=JSONResponse)
def api_orcid_coverage(mode: str = Query("doi")):
    m = resolve_mode(mode)
    return DATA[m]["orcid"]


@app.get("/api/licenses-summary", response_class=JSONResponse)
def api_licenses_summary(mode: str = Query("doi")):
    m = resolve_mode(mode)
    return DATA[m]["licenses"]


@app.get("/api/institutions", response_class=JSONResponse)
def api_institutions(mode: str = Query("doi")):
    m = resolve_mode(mode)
    return DATA[m]["institutions"]


@app.get("/api/institutions/{ror_id:path}", response_class=JSONResponse)
def api_institution_detail(ror_id: str, mode: str = Query("doi")):
    m = resolve_mode(mode)
    datasets_by_inst = DATA[m]["datasets_by_inst"]

    # ror_id může přijít URL-enkódovaný, např. 'https%3A//ror.org/024d6js02'
    ror_key = unquote(ror_id)

    datasets = datasets_by_inst.get(ror_key, [])
    return {"datasets": datasets}


# načteme data při startu
load_data()

