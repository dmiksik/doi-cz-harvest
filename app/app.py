from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
import json
import os

# Cesty k souborům – můžeš upravit podle své struktury
DATA_DIR = "processed"
ANALYSIS_DIR = "analysis"

SUMMARY_PATH = os.path.join(DATA_DIR, "summary_stats.json")
INST_PATH = os.path.join(DATA_DIR, "institutions.tsv")
DEDUP_PATH = os.path.join(DATA_DIR, "datasets_dedup.jsonl")

TIMELINE_PATH = os.path.join(ANALYSIS_DIR, "timeline.tsv")
ORCID_PATH = os.path.join(ANALYSIS_DIR, "orcid_coverage.json")
LICENSE_SUMMARY_PATH = os.path.join(ANALYSIS_DIR, "license_dataset_summary.json")

app = FastAPI()

summary = {}
institutions = {}          # ror_id -> {name, dataset_count, author_count}
datasets_by_doi = {}       # doi -> full record from datasets_dedup
dois_by_institution = {}   # ror_id -> [doi, ...]
timeline = []
orcid_coverage = {}
license_summary = {}


def load_data():
    global summary, institutions, datasets_by_doi, dois_by_institution, timeline, orcid_coverage, license_summary

    # summary_stats.json
    with open(SUMMARY_PATH, encoding="utf-8") as f:
        summary = json.load(f)

    # institutions.tsv
    institutions.clear()
    dois_by_institution.clear()
    with open(INST_PATH, encoding="utf-8") as f:
        header = next(f)  # přeskoč hlavičku
        for line in f:
            if not line.strip():
                continue
            ror_id, name, ds_count, auth_count = line.rstrip("\n").split("\t")
            institutions[ror_id] = {
                "ror_id": ror_id,
                "name": name,
                "dataset_count": int(ds_count),
                "author_count": int(auth_count),
            }
            dois_by_institution.setdefault(ror_id, [])

    # datasets_dedup.jsonl
    datasets_by_doi.clear()
    with open(DEDUP_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            doi = obj.get("doi")
            if not doi:
                continue
            datasets_by_doi[doi] = obj
            for ror_id in obj.get("ror_ids", []):
                if ror_id not in dois_by_institution:
                    dois_by_institution[ror_id] = []
                dois_by_institution[ror_id].append(doi)

    # timeline.tsv
    timeline.clear()
    if os.path.exists(TIMELINE_PATH):
        with open(TIMELINE_PATH, encoding="utf-8") as f:
            header = next(f)
            for line in f:
                if not line.strip():
                    continue
                year, total, dc, cr = line.rstrip("\n").split("\t")
                timeline.append({
                    "year": int(year),
                    "total": int(total),
                    "datacite": int(dc),
                    "crossref": int(cr),
                })

    # orcid_coverage.json
    if os.path.exists(ORCID_PATH):
        with open(ORCID_PATH, encoding="utf-8") as f:
            orcid_coverage.clear()
            orcid_coverage.update(json.load(f))

    # license_dataset_summary.json
    if os.path.exists(LICENSE_SUMMARY_PATH):
        with open(LICENSE_SUMMARY_PATH, encoding="utf-8") as f:
            license_summary.clear()
            license_summary.update(json.load(f))


load_data()


@app.get("/api/summary")
def get_summary():
    return summary


@app.get("/api/institutions")
def get_institutions():
    inst_list = [v for v in institutions.values() if v["dataset_count"] > 0]
    inst_list.sort(key=lambda x: x["dataset_count"], reverse=True)
    return inst_list


from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse  # jestli tam ještě není
@app.get("/api/institutions/{ror_id:path}")
def get_institution_detail(ror_id: str):
    """
    Vrátí detail instituce + seznam datasetů.

    Používáme {ror_id:path}, aby FastAPI korektně zvládalo ROR ID,
    i kdyby se do URL někdy dostaly ne-enkódované lomítka.
    """
    # ror_id přijde už DEKÓDOVANÝ (tj. 'https://ror.org/0xxxxx')
    inst = institutions.get(ror_id)

    # fallback – někdy se stane, že v institutions.tsv je jen suffix
    if not inst:
        # zkusíme najít institut podle konce ROR ID (0xxxxx)
        suffix = ror_id.split("/")[-1]
        for rid, meta in institutions.items():
            if rid.endswith(suffix):
                inst = meta
                ror_id = rid  # sjednotíme klíč
                break

    if not inst:
        raise HTTPException(status_code=404, detail=f"Institution not found: {ror_id}")

    dois = dois_by_institution.get(ror_id, [])
    datasets = []

    for doi in dois:
        rec = datasets_by_doi.get(doi, {})
        dc = rec.get("records", {}).get("datacite")
        cr = rec.get("records", {}).get("crossref")

        title = None
        year = None
        sources = rec.get("sources", [])

        if dc:
            attrs = dc.get("attributes") or {}
            titles = attrs.get("titles") or []
            if titles:
                title = titles[0].get("title")
            y = attrs.get("publicationYear")
            if y:
                year = y

        if cr and (title is None or year is None):
            if title is None:
                titles = cr.get("title") or []
                if titles:
                    title = titles[0]
            if year is None:
                issued = cr.get("issued") or {}
                parts = issued.get("date-parts") or []
                if parts and parts[0]:
                    year = parts[0][0]

        datasets.append({
            "doi": doi,
            "title": title,
            "year": year,
            "sources": sources,
        })

    return {"institution": inst, "datasets": datasets}


@app.get("/api/datasets/{doi:path}")
def get_dataset(doi: str):
    doi_norm = doi.lower()
    rec = datasets_by_doi.get(doi_norm)
    if not rec:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return rec


@app.get("/api/timeline")
def get_timeline():
    return timeline


@app.get("/api/orcid-coverage")
def get_orcid():
    return orcid_coverage


@app.get("/api/licenses-summary")
def get_licenses_summary():
    return license_summary


@app.get("/", response_class=HTMLResponse)
def index():
    # velmi jednoduchá statická stránka – můžeš ji později nahradit čistším HTML/JS
    return """
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
  </style>
</head>
<body>
  <h1>CZ datasets (DataCite + Crossref)</h1>
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
    <button type="button" onclick="applyYearFilter()">Filtrovat</button>
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
let currentInstitutionId = null;
let currentInstitutionName = null;
let currentInstitutionDatasets = [];

async function loadSummary() {
  const [summaryRes, orcidRes, licenseRes] = await Promise.all([
    fetch('/api/summary'),
    fetch('/api/orcid-coverage'),
    fetch('/api/licenses-summary')
  ]);
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
  const res = await fetch('/api/institutions');
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

  const filtered = currentInstitutionDatasets.filter(ds => {
    // pokud nemáme rok, dataset do filtru nebereme
    if (!ds.year) return false;
    const y = Number(ds.year);
    if (isNaN(y)) return false;
    if (from !== null && y < from) return false;
    if (to !== null && y > to) return false;
    return true;
  });

  const rows = filtered.length > 0 ? filtered : [];

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
  if (!currentInstitutionId) {
    return;
  }
  renderInstitutionDatasets();
}

async function loadInstitutionDetail(ror_id, name) {
  console.log('Načítám detail pro ROR:', ror_id);
  try {
    const res = await fetch('/api/institutions/' + encodeURIComponent(ror_id));
    if (!res.ok) {
      const text = await res.text();
      console.error('Chyba při načítání detailu instituce:', res.status, text);
      alert('Nepodařilo se načíst detail instituce (HTTP ' + res.status + '). Podívej se do konzole.');
      return;
    }
    const data = await res.json();
    console.log('Detail instituce', ror_id, data);

    currentInstitutionId = ror_id;
    currentInstitutionName = name;
    currentInstitutionDatasets = data.datasets || [];

    document.getElementById('inst-title').textContent = `Detail instituce: ${name}`;

    // při kliknutí na instituci hned použij aktuální filtr (default 2020–2025)
    renderInstitutionDatasets();
  } catch (err) {
    console.error('loadInstitutionDetail error:', err);
    alert('Nastala chyba při načítání detailu instituce. Podívej se do konzole.');
  }
}

loadSummary();
loadInstitutions();
</script>


</body>
</html>
    """

