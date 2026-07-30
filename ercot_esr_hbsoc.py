"""
ERCOT ESR HBSOC Dashboard
==========================
Pulls hourBeginningPlannedSOC for all Energy Storage Resources (ESRs)
from the ERCOT NP1-301 COP snapshot for Jan 1 – May 30, 2026.
Detects flat (unchanged) HBSOC days and renders an interactive HTML dashboard.

USAGE
-----
    pip install requests pandas openpyxl
    python ercot_esr_hbsoc.py

OUTPUT
------
    ercot_esr_hbsoc_2026.csv        — raw data
    ercot_esr_hbsoc_dashboard.html  — interactive dashboard
"""

import os, time, json, requests, pandas as pd
from datetime import datetime, timezone

# ── CREDENTIALS ──────────────────────────────────────────────────────────────
USERNAME         = os.getenv("ERCOT_USERNAME",         "YOUR_EMAIL")
PASSWORD         = os.getenv("ERCOT_PASSWORD",         "YOUR_PASSWORD")
SUBSCRIPTION_KEY = os.getenv("ERCOT_SUBSCRIPTION_KEY", "YOUR_SUBSCRIPTION_KEY")

# ── CONFIG ────────────────────────────────────────────────────────────────────
BASE_URL   = "https://api.ercot.com/api/public-reports"
AUTH_URL   = ("https://ercotb2c.b2clogin.com/ercotb2c.onmicrosoft.com"
              "/B2C_1_PUBAPI-ROPC-FLOW/oauth2/v2.0/token")
ENDPOINT   = "/np1-301/60_cop_adj_period_snapshot"
CLIENT_ID  = "fec253ea-0d06-4272-a5e6-b478baeecd70"
SCOPE      = f"openid {CLIENT_ID} offline_access"

START_DATE = "2026-01-01"
END_DATE   = "2026-05-30"
PAGE_SIZE  = 10000
SLEEP_SEC  = 0.3
ESR_CSV    = "/workspaces/ERCOT_RUC_STATUS/0-500000_1.csv"
OUTPUT_CSV = "ercot_esr_hbsoc_2026.csv"
OUTPUT_HTML= "ercot_esr_hbsoc_dashboard.html"

# ── AUTH ──────────────────────────────────────────────────────────────────────
def get_token():
    resp = requests.post(AUTH_URL, data={
        "username": USERNAME, "password": PASSWORD,
        "grant_type": "password", "scope": SCOPE,
        "client_id": CLIENT_ID, "response_type": "id_token",
    }, timeout=30)
    resp.raise_for_status()
    token = resp.json().get("id_token")
    if not token: raise ValueError("Auth failed")
    print("  Authenticated.")
    return token

def headers(token):
    return {"Authorization": f"Bearer {token}",
            "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY,
            "Accept": "application/json"}

# ── FETCH ─────────────────────────────────────────────────────────────────────
def fetch_page(hdrs, page):
    resp = requests.get(f"{BASE_URL}{ENDPOINT}", headers=hdrs, params={
        "deliveryDateFrom": START_DATE, "deliveryDateTo": END_DATE,
        "size": PAGE_SIZE, "page": page,
    }, timeout=60)
    if resp.status_code == 401: raise PermissionError("401 Unauthorized")
    resp.raise_for_status()
    return resp.json()

def fetch_all(token, esr_ids):
    hdrs = headers(token)
    all_rows, page, total = [], 1, None
    print(f"\nFetching COP data: {START_DATE} → {END_DATE}\n")
    while True:
        print(f"  Page {page}" + (f"/{-(-total//PAGE_SIZE)}" if total else "") + " ...", end=" ")
        data = fetch_page(hdrs, page)
        meta   = data.get("_meta", {})
        fields = data.get("fields", [])
        rows   = data.get("data", [])
        if fields and isinstance(fields[0], dict):
            fields = [f["name"] for f in fields]
        if total is None:
            total = meta.get("totalRecords", 0)
            print(f"Total records: {total:,} | Columns: {fields}")
        if not rows:
            break
        kept = 0
        for row in rows:
            r = dict(zip(fields, row))
            # Filter to ESRs only
            if r.get("resourceName") in esr_ids:
                all_rows.append(r)
                kept += 1
        print(f"  {len(rows):,} rows, {kept} ESR rows kept (total ESR: {len(all_rows):,})")
        if page * PAGE_SIZE >= total or len(rows) < PAGE_SIZE:
            break
        page += 1
        time.sleep(SLEEP_SEC)
    return pd.DataFrame(all_rows)

# ── PROCESS ───────────────────────────────────────────────────────────────────
def process(df, asset_meta):
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={
        "resourcename": "resource",
        "deliverydate": "date",
        "hourending":   "hourending",
        "highsustainedlimit": "hsl",
        "hourbeginningplannedsoc": "hbsoc",
        "qsename": "qse",
    })
    df["date"]   = pd.to_datetime(df["date"])
    df["hbsoc"]  = pd.to_numeric(df["hbsoc"],  errors="coerce")
    df["hsl"]    = pd.to_numeric(df["hsl"],     errors="coerce")

    # Parse HE (hour value IS the HE number)
    def to_he(t):
        try:
            h = int(str(t).split(":")[0])
            return h if h > 0 else 24
        except: return None
    df["he"] = df["hourending"].apply(to_he)

    # Merge in asset metadata (asset name, rated_power_mw, zone, owner)
    df = df.merge(asset_meta[["generator_id","asset_name","rated_power_mw","zone","owner","qse_name"]],
                  left_on="resource", right_on="generator_id", how="left")

    # Flag flat days: all 24 HBSOC values identical for (date, resource)
    def is_flat(vals):
        v = vals.dropna()
        return len(v) >= 2 and v.nunique() == 1
    flat_flags = (df.groupby(["resource","date"])["hbsoc"]
                    .apply(is_flat)
                    .reset_index()
                    .rename(columns={"hbsoc":"is_flat"}))
    df = df.merge(flat_flags, on=["resource","date"], how="left")
    df["is_flat"] = df["is_flat"].fillna(False)

    df = df.sort_values(["resource","date","he"]).reset_index(drop=True)
    return df

# ── BUILD DASHBOARD ───────────────────────────────────────────────────────────
def build_dashboard(df):
    # Prepare data structure for JS
    # Group by resource → date → list of (he, hbsoc)
    resources = sorted(df["resource"].dropna().unique())

    # Build per-resource summary for dropdown
    summary = []
    for res in resources:
        rdf = df[df["resource"] == res]
        meta_row = rdf.iloc[0]
        flat_days = rdf[rdf["is_flat"]]["date"].dt.strftime("%m/%d/%Y").unique().tolist()
        dates = sorted(rdf["date"].unique())
        series = {}
        for d in dates:
            ddf = rdf[rdf["date"] == d].sort_values("he")
            key = pd.Timestamp(d).strftime("%m/%d/%Y")
            series[key] = {
                "he":    ddf["he"].tolist(),
                "hbsoc": ddf["hbsoc"].tolist(),
                "flat":  bool(ddf["is_flat"].any()),
            }
        summary.append({
            "resource":    res,
            "asset_name":  str(meta_row.get("asset_name", res)),
            "qse":         str(meta_row.get("qse_name", meta_row.get("qse", ""))),
            "hsl":         float(meta_row.get("hsl", meta_row.get("rated_power_mw", 0)) or 0),
            "zone":        str(meta_row.get("zone", "")),
            "owner":       str(meta_row.get("owner", "")),
            "flat_days":   flat_days,
            "flat_count":  len(flat_days),
            "series":      series,
        })

    # Sort by flat_count desc so worst offenders are at top
    summary.sort(key=lambda x: (-x["flat_count"], x["asset_name"]))

    data_json = json.dumps(summary)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ERCOT ESR HBSOC Dashboard — Jan–May 2026</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
  :root {{
    --navy: #0D1B3E; --steel: #1C3A6E; --ice: #C8D8F0;
    --red: #C0392B; --amber: #D68910; --green: #1D6A39;
    --bg: #F0F4FA; --white: #FFFFFF; --muted: #8A9AB5;
    --border: #D1DAE8; --text: #1E293B;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: Arial, sans-serif; background: var(--bg); color: var(--text); }}

  /* Header */
  .header {{ background: var(--navy); color: white; padding: 18px 28px; display: flex; align-items: center; justify-content: space-between; }}
  .header h1 {{ font-size: 18px; font-weight: 700; }}
  .header .sub {{ font-size: 12px; color: var(--ice); margin-top: 3px; }}
  .badge {{ background: var(--red); color: white; font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 4px; }}

  /* Layout */
  .layout {{ display: grid; grid-template-columns: 320px 1fr; height: calc(100vh - 62px); }}

  /* Sidebar */
  .sidebar {{ background: var(--white); border-right: 1px solid var(--border); display: flex; flex-direction: column; overflow: hidden; }}
  .sidebar-head {{ padding: 14px 16px; border-bottom: 1px solid var(--border); }}
  .sidebar-head input {{ width: 100%; padding: 7px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; outline: none; }}
  .sidebar-head input:focus {{ border-color: var(--steel); }}
  .filter-row {{ display: flex; gap: 6px; margin-top: 8px; }}
  .filter-btn {{ flex: 1; padding: 5px 0; font-size: 11px; font-weight: 600; border: 1px solid var(--border); border-radius: 4px; cursor: pointer; background: var(--white); color: var(--text); transition: all 0.15s; }}
  .filter-btn.active {{ background: var(--navy); color: white; border-color: var(--navy); }}
  .asset-list {{ flex: 1; overflow-y: auto; }}
  .asset-item {{ padding: 10px 16px; border-bottom: 1px solid var(--border); cursor: pointer; transition: background 0.1s; }}
  .asset-item:hover {{ background: var(--bg); }}
  .asset-item.selected {{ background: #E2EAF6; border-left: 3px solid var(--steel); }}
  .asset-item.has-flat {{ border-left: 3px solid var(--red); }}
  .asset-item.selected.has-flat {{ background: #FDECEA; border-left: 3px solid var(--red); }}
  .asset-name {{ font-size: 13px; font-weight: 600; color: var(--navy); }}
  .asset-meta {{ font-size: 11px; color: var(--muted); margin-top: 2px; }}
  .flat-badge {{ display: inline-block; background: var(--red); color: white; font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 3px; margin-left: 6px; }}

  /* Main panel */
  .main {{ display: flex; flex-direction: column; overflow: hidden; }}
  .asset-header {{ padding: 16px 24px; background: var(--white); border-bottom: 1px solid var(--border); }}
  .asset-header h2 {{ font-size: 16px; font-weight: 700; color: var(--navy); }}
  .meta-pills {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }}
  .pill {{ background: var(--bg); border: 1px solid var(--border); border-radius: 4px; padding: 3px 10px; font-size: 11px; color: var(--text); }}
  .pill strong {{ color: var(--navy); }}
  .flat-alert {{ background: #FDECEA; border: 1px solid #F5C6C6; border-radius: 6px; padding: 8px 14px; margin-top: 10px; font-size: 12px; color: var(--red); }}

  /* Chart controls */
  .chart-controls {{ padding: 10px 24px; background: var(--white); border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
  .ctrl-label {{ font-size: 12px; font-weight: 600; color: var(--muted); }}
  .month-btn {{ padding: 4px 12px; font-size: 12px; border: 1px solid var(--border); border-radius: 4px; cursor: pointer; background: var(--white); transition: all 0.15s; }}
  .month-btn.active {{ background: var(--steel); color: white; border-color: var(--steel); }}
  .flat-toggle {{ margin-left: auto; display: flex; align-items: center; gap: 6px; font-size: 12px; cursor: pointer; }}
  .flat-toggle input {{ cursor: pointer; }}

  /* Chart area */
  .chart-wrap {{ flex: 1; padding: 20px 24px; overflow: hidden; position: relative; }}
  canvas {{ max-height: 100% !important; }}

  /* Empty state */
  .empty {{ display: flex; align-items: center; justify-content: center; height: 100%; color: var(--muted); font-size: 14px; }}

  /* Summary bar */
  .summary-bar {{ padding: 8px 24px; background: var(--navy); color: var(--ice); font-size: 11px; display: flex; gap: 24px; }}
  .summary-bar span {{ color: white; font-weight: 700; }}
</style>
</head>
<body>

<div class="header">
  <div>
    <div class="h1" style="font-size:18px;font-weight:700;">ERCOT ESR — Hour Beginning Planned SOC</div>
    <div class="sub">Energy Storage Resources · Jan 1 – May 30, 2026 · Source: NP1-301 COP Snapshot</div>
  </div>
  <div class="badge" id="flat-count-badge">Loading...</div>
</div>

<div class="layout">
  <!-- Sidebar -->
  <div class="sidebar">
    <div class="sidebar-head">
      <input type="text" id="search" placeholder="Search asset or QSE...">
      <div class="filter-row">
        <button class="filter-btn active" onclick="setFilter('all',this)">All Assets</button>
        <button class="filter-btn" onclick="setFilter('flat',this)">Flat Days Only</button>
        <button class="filter-btn" onclick="setFilter('clean',this)">No Flat Days</button>
      </div>
    </div>
    <div class="asset-list" id="asset-list"></div>
  </div>

  <!-- Main -->
  <div class="main">
    <div class="asset-header" id="asset-header">
      <div class="empty">← Select an asset to view HBSOC profile</div>
    </div>
    <div class="chart-controls" id="chart-controls" style="display:none;">
      <span class="ctrl-label">MONTH:</span>
      <div id="month-btns"></div>
      <label class="flat-toggle">
        <input type="checkbox" id="show-flat-only"> Highlight flat days only
      </label>
    </div>
    <div class="chart-wrap">
      <canvas id="chart" style="display:none;"></canvas>
      <div class="empty" id="empty-msg">← Select an asset</div>
    </div>
    <div class="summary-bar" id="summary-bar">
      <div>Total ESRs: <span id="s-total">-</span></div>
      <div>ESRs with flat days: <span id="s-flat">-</span></div>
      <div>Total flat day-instances: <span id="s-flat-days">-</span></div>
      <div>Date range: <span>Jan 1 – May 30, 2026</span></div>
    </div>
  </div>
</div>

<script>
const ALL_DATA = {data_json};

// Palette — cycle through for dates
const PALETTE = [
  '#1C3A6E','#C0392B','#D68910','#1D6A39','#7B2D8B',
  '#0891B2','#EA580C','#4F46E5','#059669','#DC2626',
  '#2563EB','#D97706','#7C3AED','#0F766E','#BE185D',
];
const FLAT_COLOR = 'rgba(192,57,43,0.85)';

let currentAsset = null;
let currentFilter = 'all';
let currentMonth = 'all';
let chartInstance = null;

// ── Build sidebar ────────────────────────────────────────────────────────
function renderSidebar(filter='all', search='') {{
  const list = document.getElementById('asset-list');
  list.innerHTML = '';
  const s = search.toLowerCase();
  let shown = 0;
  ALL_DATA.forEach((a, idx) => {{
    if (filter === 'flat'  && a.flat_count === 0) return;
    if (filter === 'clean' && a.flat_count > 0)  return;
    if (s && !a.asset_name.toLowerCase().includes(s) && !a.qse.toLowerCase().includes(s) && !a.resource.toLowerCase().includes(s)) return;
    const div = document.createElement('div');
    div.className = 'asset-item' + (a.flat_count > 0 ? ' has-flat' : '') + (currentAsset === idx ? ' selected' : '');
    div.innerHTML = `
      <div class="asset-name">${{a.asset_name}}${{a.flat_count > 0 ? `<span class="flat-badge">${{a.flat_count}} flat</span>` : ''}}</div>
      <div class="asset-meta">${{a.resource}} · ${{a.hsl.toFixed(1)}} MW · ${{a.zone}}</div>
      <div class="asset-meta">${{a.qse}}</div>
    `;
    div.onclick = () => selectAsset(idx);
    list.appendChild(div);
    shown++;
  }});
  // Update summary
  const total = ALL_DATA.length;
  const flatCount = ALL_DATA.filter(a => a.flat_count > 0).length;
  const flatDays = ALL_DATA.reduce((s,a) => s + a.flat_count, 0);
  document.getElementById('s-total').textContent = total;
  document.getElementById('s-flat').textContent = flatCount;
  document.getElementById('s-flat-days').textContent = flatDays;
  document.getElementById('flat-count-badge').textContent = `${{flatCount}} ESRs with flat HBSOC days`;
}}

// ── Select asset ─────────────────────────────────────────────────────────
function selectAsset(idx) {{
  currentAsset = idx;
  currentMonth = 'all';
  renderSidebar(currentFilter, document.getElementById('search').value);

  const a = ALL_DATA[idx];
  // Header
  const hdr = document.getElementById('asset-header');
  const flatHtml = a.flat_count > 0
    ? `<div class="flat-alert">⚠ ${{a.flat_count}} day(s) with flat (unchanged) HBSOC detected: ${{a.flat_days.join(', ')}}</div>`
    : '';
  hdr.innerHTML = `
    <h2>${{a.asset_name}} <span style="font-weight:400;font-size:14px;color:#8A9AB5;">${{a.resource}}</span></h2>
    <div class="meta-pills">
      <div class="pill"><strong>QSE:</strong> ${{a.qse}}</div>
      <div class="pill"><strong>HSL:</strong> ${{a.hsl.toFixed(1)}} MW</div>
      <div class="pill"><strong>Zone:</strong> ${{a.zone}}</div>
      <div class="pill"><strong>Owner:</strong> ${{a.owner}}</div>
      <div class="pill"><strong>Days w/ data:</strong> ${{Object.keys(a.series).length}}</div>
      <div class="pill"><strong>Flat days:</strong> <span style="color:${{a.flat_count>0?'#C0392B':'#1D6A39'}};font-weight:700;">${{a.flat_count}}</span></div>
    </div>
    ${{flatHtml}}
  `;

  // Month buttons
  const months = [...new Set(Object.keys(a.series).map(d => d.slice(0,2) + '/2026'))].sort();
  const monthLabels = {{'01':'Jan','02':'Feb','03':'Mar','04':'Apr','05':'May'}};
  const btnDiv = document.getElementById('month-btns');
  btnDiv.innerHTML = '<button class="month-btn active" onclick="setMonth(\'all\',this)">All</button>' +
    months.map(m => `<button class="month-btn" onclick="setMonth('${{m}}',this)">${{monthLabels[m.slice(0,2)] || m}}</button>`).join('');

  document.getElementById('chart-controls').style.display = 'flex';
  renderChart();
}}

function setMonth(m, btn) {{
  currentMonth = m;
  document.querySelectorAll('.month-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderChart();
}}

function setFilter(f, btn) {{
  currentFilter = f;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderSidebar(f, document.getElementById('search').value);
}}

// ── Render chart ─────────────────────────────────────────────────────────
function renderChart() {{
  if (currentAsset === null) return;
  const a = ALL_DATA[currentAsset];
  const flatOnly = document.getElementById('show-flat-only').checked;

  let dates = Object.keys(a.series).sort();
  if (currentMonth !== 'all') {{
    dates = dates.filter(d => d.startsWith(currentMonth.slice(0,2)));
  }}
  if (flatOnly) {{
    dates = dates.filter(d => a.series[d].flat);
  }}

  document.getElementById('chart').style.display = 'block';
  document.getElementById('empty-msg').style.display = 'none';

  const datasets = dates.map((d, i) => {{
    const s = a.series[d];
    const isFlat = s.flat;
    return {{
      label: d,
      data: s.he.map((h, j) => ({{ x: h, y: s.hbsoc[j] }})),
      borderColor: isFlat ? FLAT_COLOR : PALETTE[i % PALETTE.length],
      backgroundColor: 'transparent',
      borderWidth: isFlat ? 2.5 : 1.2,
      borderDash: isFlat ? [] : [],
      pointRadius: 2,
      pointHoverRadius: 5,
      tension: 0.2,
    }};
  }});

  if (chartInstance) chartInstance.destroy();
  const ctx = document.getElementById('chart').getContext('2d');
  chartInstance = new Chart(ctx, {{
    type: 'line',
    data: {{ datasets }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      interaction: {{ mode: 'nearest', intersect: false }},
      plugins: {{
        legend: {{
          display: dates.length <= 30,
          position: 'right',
          labels: {{
            boxWidth: 12, font: {{ size: 10 }},
            generateLabels: chart => chart.data.datasets.map((ds, i) => ({{
              text: ds.label + (a.series[ds.label]?.flat ? ' ⚠' : ''),
              fillStyle: ds.borderColor,
              strokeStyle: ds.borderColor,
              lineWidth: ds.borderWidth,
              hidden: false,
              index: i,
            }}))
          }}
        }},
        tooltip: {{
          callbacks: {{
            title: items => `${{items[0].dataset.label}} · HE${{items[0].parsed.x}}`,
            label: item => `HBSOC: ${{item.parsed.y?.toFixed(1) ?? 'N/A'}}%${{a.series[item.dataset.label]?.flat ? '  ⚠ FLAT DAY' : ''}}`,
          }}
        }},
        title: {{
          display: true,
          text: `${{a.asset_name}} — Hour Beginning Planned SOC (%)`
            + (currentMonth !== 'all' ? ` · ${{['Jan','Feb','Mar','Apr','May'][parseInt(currentMonth)-1]}} 2026` : ' · Jan–May 2026'),
          color: '#0D1B3E', font: {{ size: 13, weight: 'bold' }},
        }},
      }},
      scales: {{
        x: {{
          type: 'linear', min: 1, max: 24,
          title: {{ display: true, text: 'Hour Ending', color: '#8A9AB5', font: {{ size: 11 }} }},
          ticks: {{ stepSize: 1, callback: v => `HE${{v}}`, color: '#8A9AB5', font: {{ size: 10 }} }},
          grid: {{ color: '#E2EAF6' }},
        }},
        y: {{
          min: 0, max: 105,
          title: {{ display: true, text: 'HBSOC (%)', color: '#8A9AB5', font: {{ size: 11 }} }},
          ticks: {{ callback: v => v + '%', color: '#8A9AB5', font: {{ size: 10 }} }},
          grid: {{ color: '#E2EAF6' }},
        }},
      }},
    }},
  }});
}}

// ── Init ─────────────────────────────────────────────────────────────────
renderSidebar();
document.getElementById('search').addEventListener('input', e => {{
  renderSidebar(currentFilter, e.target.value);
}});
document.getElementById('show-flat-only').addEventListener('change', renderChart);
</script>
</body>
</html>"""

    with open(OUTPUT_HTML, "w") as f:
        f.write(html)
    print(f"  Saved dashboard: {OUTPUT_HTML}")


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  ERCOT ESR HBSOC Dashboard")
    print(f"  Date range: {START_DATE} → {END_DATE}")
    print("=" * 60)

    if "YOUR_EMAIL" in USERNAME:
        print("\n⚠  Set credentials as env vars and re-run.")
        return

    # Load ESR asset list
    print("\nLoading ESR asset list ...")
    esr_df = pd.read_csv(ESR_CSV)
    esr_df["valid_from"] = pd.to_datetime(esr_df["valid_from"])
    esr_df["valid_to"]   = pd.to_datetime(esr_df["valid_to"])
    # Keep records active during our window
    active = esr_df[esr_df["valid_to"] >= START_DATE].copy()
    active = active.sort_values("valid_from", ascending=False).drop_duplicates("generator_id")
    active = active.dropna(subset=["generator_id"])
    active = active.rename(columns={"asset":"asset_name","qse":"qse_name"})
    esr_ids = set(active["generator_id"].tolist())
    print(f"  {len(esr_ids)} active ESR generator IDs to filter for")

    # Auth + fetch
    print("\nStep 1: Authenticating ...")
    token = get_token()

    print("\nStep 2: Fetching COP data ...")
    df = fetch_all(token, esr_ids)

    if df.empty:
        print("No data returned.")
        return

    # Process
    print("\nStep 3: Processing ...")
    asset_meta = active[["generator_id","asset_name","rated_power_mw","zone","owner","qse_name"]].copy()
    df = process(df, asset_meta)

    # Save CSV
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"  Saved {len(df):,} records → {OUTPUT_CSV}")

    # Stats
    dedup = df.drop_duplicates(subset=["resource","date"])
    flat  = dedup[dedup["is_flat"]]
    print(f"\n  ESRs with data:      {df['resource'].nunique()}")
    print(f"  Total day-records:   {len(dedup):,}")
    print(f"  Flat HBSOC days:     {len(flat):,} across {flat['resource'].nunique()} assets")

    # Build dashboard
    print("\nStep 4: Building dashboard ...")
    build_dashboard(df)
    print("\nDone! Download ercot_esr_hbsoc_dashboard.html to open in your browser.")

if __name__ == "__main__":
    main()
