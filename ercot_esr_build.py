"""
ERCOT ESR HBSOC — Build Dashboard
====================================
Step 2 of 2. Reads ercot_esr_hbsoc_raw.csv (output of ercot_esr_fetch.py)
and builds the interactive HTML dashboard. Runs in seconds, no API calls.

USAGE
-----
    pip install pandas
    python ercot_esr_build.py

INPUT
-----
    ercot_esr_hbsoc_raw.csv   — from ercot_esr_fetch.py
    0-500000_1.csv            — ESR asset metadata

OUTPUT
------
    ercot_esr_hbsoc_dashboard.html
"""

import os, json
import pandas as pd

RAW_CSV    = "ercot_esr_hbsoc_raw.csv"
ESR_CSV    = "0-500000_1.csv"
OUTPUT_HTML= "ercot_esr_hbsoc_dashboard.html"
START_DATE = "2026-01-01"
END_DATE   = "2026-05-30"

# ── LOAD & PROCESS ────────────────────────────────────────────────────────────
def load_and_process():
    print(f"Loading {RAW_CSV} ...")
    df = pd.read_csv(RAW_CSV, low_memory=False)
    print(f"  {len(df):,} raw rows")

    df = df.rename(columns={
        "deliveryDate":           "date",
        "qseName":                "qse",
        "resourceName":           "resource",
        "hourEnding":             "hourending",
        "highSustainedLimit":     "hsl",
        "hourBeginningPlannedSOC":"hbsoc",
    })

    df["date"]  = pd.to_datetime(df["date"])
    df["hbsoc"] = pd.to_numeric(df["hbsoc"], errors="coerce")
    df["hsl"]   = pd.to_numeric(df["hsl"],   errors="coerce")

    # Calculate SOC as a percentage of HSL
    # HBSOC is in MW — divide by HSL (MW) and multiply by 100 to get %
    df["soc_pct"] = (df["hbsoc"] / df["hsl"] * 100).round(1)
    # Clamp to 0-100 in case of any data anomalies
    df["soc_pct"] = df["soc_pct"].clip(0, 100)

    # Parse HE: 01:00 = HE1, 24:00 = HE24
    def to_he(t):
        try:
            h = int(str(t).split(":")[0])
            return h if h > 0 else 24
        except: return None
    df["he"] = df["hourending"].apply(to_he)

    # Deduplicate to one row per (resource, date, he) — keep last COP revision
    before = len(df)
    df = df.drop_duplicates(subset=["resource","date","he"], keep="last")
    print(f"  After dedup: {len(df):,} rows (removed {before-len(df):,} duplicate COP revisions)")

    # Load asset metadata
    print(f"Loading {ESR_CSV} ...")
    meta = pd.read_csv(ESR_CSV)
    meta["valid_to"] = pd.to_datetime(meta["valid_to"])
    meta = meta[meta["valid_to"] >= START_DATE]
    meta = meta.sort_values("valid_from", ascending=False).drop_duplicates("generator_id")
    meta = meta.dropna(subset=["generator_id"])
    meta = meta.rename(columns={"asset":"asset_name","qse":"qse_name"})

    # Flag flat days: all HE readings identical for (resource, date)
    def is_flat(vals):
        v = vals.dropna()
        return len(v) >= 2 and v.nunique() == 1

    print("Detecting flat HBSOC days ...")
    flat_flags = (df.groupby(["resource","date"])["hbsoc"]
                    .apply(is_flat).reset_index()
                    .rename(columns={"hbsoc":"is_flat"}))
    df = df.merge(flat_flags, on=["resource","date"], how="left")
    df["is_flat"] = df["is_flat"].fillna(False)

    # Merge metadata
    df = df.merge(
        meta[["generator_id","asset_name","rated_power_mw","zone","owner","qse_name"]],
        left_on="resource", right_on="generator_id", how="left"
    )

    df = df.sort_values(["resource","date","he"]).reset_index(drop=True)

    dedup_day = df.drop_duplicates(subset=["resource","date"])
    flat      = dedup_day[dedup_day["is_flat"]]
    print(f"\n  ESRs with data:     {df['resource'].nunique()}")
    print(f"  Operating days:     {dedup_day['date'].nunique()}")
    print(f"  Flat HBSOC days:    {len(flat):,} across {flat['resource'].nunique()} assets")

    return df

# ── BUILD DASHBOARD JSON ──────────────────────────────────────────────────────
def build_summary(df):
    print("\nBuilding dashboard data ...")
    summary = []
    resources = sorted(df["resource"].dropna().unique())

    for res in resources:
        rdf = df[df["resource"] == res]
        meta_row = rdf.iloc[0]
        flat_days = (rdf[rdf["is_flat"]]["date"]
                     .drop_duplicates()
                     .dt.strftime("%-m/%-d/%Y")
                     .tolist())
        series = {}
        for d, ddf in rdf.groupby("date"):
            ddf = ddf.sort_values("he")
            key = pd.Timestamp(d).strftime("%-m/%-d/%Y")
            series[key] = {
                "he":      ddf["he"].tolist(),
                "hbsoc":   [round(v, 1) if pd.notna(v) else None for v in ddf["hbsoc"].tolist()],
                "soc_pct": [round(v, 1) if pd.notna(v) else None for v in ddf["soc_pct"].tolist()],
                "flat":    bool(ddf["is_flat"].any()),
            }
        summary.append({
            "resource":   res,
            "asset_name": str(meta_row.get("asset_name", res)),
            "qse":        str(meta_row.get("qse_name", meta_row.get("qse", ""))),
            "hsl":        float(meta_row.get("hsl", meta_row.get("rated_power_mw", 0)) or 0),
            "zone":       str(meta_row.get("zone", "")),
            "owner":      str(meta_row.get("owner", "")),
            "flat_days":  flat_days,
            "flat_count": len(flat_days),
            "series":     series,
        })

    summary.sort(key=lambda x: (-x["flat_count"], x["asset_name"]))
    print(f"  {len(summary)} assets in dashboard")
    return summary

# ── BUILD HTML ────────────────────────────────────────────────────────────────
def build_html(summary, total_esrs, flat_esr_count, flat_day_total):
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
    --navy:#0D1B3E;--steel:#1C3A6E;--ice:#C8D8F0;
    --red:#C0392B;--amber:#D68910;--green:#1D6A39;
    --bg:#F0F4FA;--white:#FFFFFF;--muted:#8A9AB5;
    --border:#D1DAE8;--text:#1E293B;
  }}
  *{{box-sizing:border-box;margin:0;padding:0;}}
  body{{font-family:Arial,sans-serif;background:var(--bg);color:var(--text);}}
  .header{{background:var(--navy);color:white;padding:16px 24px;display:flex;align-items:center;justify-content:space-between;}}
  .header h1{{font-size:17px;font-weight:700;}}
  .header .sub{{font-size:11px;color:var(--ice);margin-top:3px;}}
  .badge{{background:var(--red);color:white;font-size:11px;font-weight:700;padding:3px 10px;border-radius:4px;white-space:nowrap;}}
  .layout{{display:grid;grid-template-columns:300px 1fr;height:calc(100vh - 58px);}}
  .sidebar{{background:var(--white);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden;}}
  .sb-head{{padding:12px 14px;border-bottom:1px solid var(--border);}}
  .sb-head input{{width:100%;padding:6px 10px;border:1px solid var(--border);border-radius:6px;font-size:12px;outline:none;}}
  .sb-head input:focus{{border-color:var(--steel);}}
  .filter-row{{display:flex;gap:5px;margin-top:7px;}}
  .fbt{{flex:1;padding:4px 0;font-size:11px;font-weight:600;border:1px solid var(--border);border-radius:4px;cursor:pointer;background:var(--white);}}
  .fbt.on{{background:var(--navy);color:white;border-color:var(--navy);}}
  .asset-list{{flex:1;overflow-y:auto;}}
  .ai{{padding:9px 14px;border-bottom:1px solid var(--border);cursor:pointer;border-left:3px solid transparent;}}
  .ai:hover{{background:var(--bg);}}
  .ai.sel{{background:#E2EAF6;border-left-color:var(--steel);}}
  .ai.flat{{border-left-color:var(--red);}}
  .ai.sel.flat{{background:#FDECEA;border-left-color:var(--red);}}
  .ai-name{{font-size:12px;font-weight:600;color:var(--navy);}}
  .ai-sub{{font-size:10px;color:var(--muted);margin-top:1px;}}
  .fbadge{{display:inline-block;background:var(--red);color:white;font-size:9px;font-weight:700;padding:1px 5px;border-radius:3px;margin-left:5px;}}
  .main{{display:flex;flex-direction:column;overflow:hidden;background:var(--bg);}}
  .asset-hdr{{padding:12px 20px;background:var(--white);border-bottom:1px solid var(--border);}}
  .asset-hdr h2{{font-size:14px;font-weight:700;color:var(--navy);}}
  .pills{{display:flex;flex-wrap:wrap;gap:6px;margin-top:7px;}}
  .pill{{background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:2px 8px;font-size:11px;}}
  .pill strong{{color:var(--steel);}}
  .flat-alert{{background:#FDECEA;border:1px solid #F5C6C6;border-radius:4px;padding:6px 10px;margin-top:8px;font-size:11px;color:var(--red);}}
  .ctrl-bar{{padding:7px 20px;background:var(--white);border-bottom:1px solid var(--border);display:flex;gap:6px;align-items:center;flex-wrap:wrap;}}
  .cl{{font-size:11px;color:var(--muted);font-weight:600;}}
  .mbt{{font-size:11px;padding:3px 9px;border:1px solid var(--border);border-radius:4px;cursor:pointer;background:var(--white);}}
  .mbt.on{{background:var(--steel);color:white;border-color:var(--steel);}}
  .flat-toggle{{margin-left:auto;display:flex;align-items:center;gap:5px;font-size:11px;cursor:pointer;color:var(--muted);}}
  .chart-wrap{{flex:1;padding:16px 20px;overflow:hidden;position:relative;}}
  .empty{{display:flex;align-items:center;justify-content:center;height:100%;color:var(--muted);font-size:13px;}}
  .sum-bar{{padding:7px 20px;background:var(--navy);color:var(--ice);font-size:11px;display:flex;gap:24px;flex-shrink:0;}}
  .sum-bar b{{color:white;}}
</style>
</head>
<body>
<div class="header">
  <div>
    <div class="header h1" style="font-size:17px;font-weight:700;">ERCOT ESR — Hour Beginning Planned SOC</div>
    <div class="sub">Energy Storage Resources · Jan 1 – May 30, 2026 · HBSOC shown as % of HSL · Source: NP1-301 COP Snapshot</div>
  </div>
  <div class="badge" id="flat-badge">Loading...</div>
</div>
<div class="layout">
  <div class="sidebar">
    <div class="sb-head">
      <input type="text" id="search" placeholder="Search asset or QSE...">
      <div class="filter-row">
        <button class="fbt on" onclick="setFilter('all',this)">All Assets</button>
        <button class="fbt" onclick="setFilter('flat',this)">Flat Days Only</button>
        <button class="fbt" onclick="setFilter('clean',this)">No Flat Days</button>
      </div>
    </div>
    <div class="asset-list" id="asset-list"></div>
  </div>
  <div class="main">
    <div class="asset-hdr" id="asset-hdr">
      <div class="empty">← Select an asset to view its HBSOC profile</div>
    </div>
    <div class="ctrl-bar" id="ctrl-bar" style="display:none;">
      <span class="cl">MONTH:</span>
      <div id="month-btns"></div>
      <label class="flat-toggle"><input type="checkbox" id="flat-only"> Show flat days only</label>
    </div>
    <div class="chart-wrap">
      <canvas id="chart" style="display:none;"></canvas>
      <div class="empty" id="empty-msg">← Select an asset</div>
    </div>
    <div class="sum-bar">
      <div>Total ESRs: <b id="s-total">{total_esrs}</b></div>
      <div>ESRs with flat days: <b id="s-flat">{flat_esr_count}</b></div>
      <div>Total flat day-instances: <b id="s-flat-days">{flat_day_total}</b></div>
      <div>Range: <b>Jan 1 – May 30, 2026</b></div>
    </div>
  </div>
</div>
<script>
const D={data_json};
const PAL=['#1C3A6E','#7B2D8B','#0891B2','#059669','#D97706','#4F46E5','#0F766E','#BE185D','#1D4ED8','#065F46','#92400E','#6B21A8','#0E7490','#166534','#9A3412'];
const FLAT_C='#C0392B';
let cur=null,curFilter='all',curMonth='all',chartInst=null;

document.getElementById('flat-badge').textContent=`{flat_esr_count} ESRs with flat HBSOC days`;

function renderList(filter,search){{
  const el=document.getElementById('asset-list');
  el.innerHTML='';
  const s=search.toLowerCase();
  D.forEach((a,i)=>{{
    if(filter==='flat'&&a.flat_count===0)return;
    if(filter==='clean'&&a.flat_count>0)return;
    if(s&&!a.asset_name.toLowerCase().includes(s)&&!a.qse.toLowerCase().includes(s)&&!a.resource.toLowerCase().includes(s))return;
    const d=document.createElement('div');
    d.className='ai'+(a.flat_count>0?' flat':'')+(cur===i?' sel':'');
    d.innerHTML=`<div class="ai-name">${{a.asset_name}}${{a.flat_count>0?`<span class="fbadge">${{a.flat_count}} flat</span>`:''}}</div>
      <div class="ai-sub">${{a.resource}} · ${{a.hsl.toFixed(1)}} MW · ${{a.zone}}</div>
      <div class="ai-sub">${{a.qse}}</div>`;
    d.onclick=()=>selectAsset(i);
    el.appendChild(d);
  }});
}}

function selectAsset(i){{
  cur=i;curMonth='all';
  renderList(curFilter,document.getElementById('search').value);
  const a=D[i];
  const hdr=document.getElementById('asset-hdr');
  hdr.innerHTML=`<h2>${{a.asset_name}} <span style="font-weight:400;font-size:12px;color:var(--muted)">${{a.resource}}</span></h2>
    <div class="pills">
      <div class="pill"><strong>QSE:</strong> ${{a.qse}}</div>
      <div class="pill"><strong>HSL:</strong> ${{a.hsl.toFixed(1)}} MW</div>
      <div class="pill"><strong>Zone:</strong> ${{a.zone}}</div>
      <div class="pill"><strong>Owner:</strong> ${{a.owner}}</div>
      <div class="pill"><strong>Days w/ data:</strong> ${{Object.keys(a.series).length}}</div>
      <div class="pill"><strong>Flat days:</strong> <span style="color:${{a.flat_count>0?'var(--red)':'var(--green)'}};font-weight:700">${{a.flat_count}}</span></div>
    </div>
    ${{a.flat_count>0?`<div class="flat-alert">⚠ ${{a.flat_count}} flat HBSOC day(s): ${{a.flat_days.slice(0,10).join(', ')}}${{a.flat_days.length>10?' +more':''}}</div>`:''}}`;

  const months=[...new Set(Object.keys(a.series).map(d=>d.slice(0,2)))].sort();
  const ml={{'01':'Jan','02':'Feb','03':'Mar','04':'Apr','05':'May'}};
  document.getElementById('month-btns').innerHTML=
    `<button class="mbt on" onclick="setMonth('all',this)">All</button>`+
    months.map(m=>`<button class="mbt" onclick="setMonth('${{m}}',this)">${{ml[m]||m}}</button>`).join('');
  document.getElementById('ctrl-bar').style.display='flex';
  renderChart();
}}

function setFilter(f,btn){{
  curFilter=f;
  document.querySelectorAll('.fbt').forEach(b=>b.classList.remove('on'));
  btn.classList.add('on');
  renderList(f,document.getElementById('search').value);
}}

function setMonth(m,btn){{
  curMonth=m;
  document.querySelectorAll('.mbt').forEach(b=>b.classList.remove('on'));
  btn.classList.add('on');
  renderChart();
}}

function renderChart(){{
  if(cur===null)return;
  const a=D[cur];
  const flatOnly=document.getElementById('flat-only').checked;
  let dates=Object.keys(a.series).sort();
  if(curMonth!=='all')dates=dates.filter(d=>d.startsWith(curMonth));
  if(flatOnly)dates=dates.filter(d=>a.series[d].flat);
  if(!dates.length){{
    if(chartInst){{chartInst.destroy();chartInst=null;}}
    document.getElementById('chart').style.display='none';
    document.getElementById('empty-msg').style.display='flex';
    document.getElementById('empty-msg').textContent='No flat days in this period';
    return;
  }}
  document.getElementById('chart').style.display='block';
  document.getElementById('empty-msg').style.display='none';
  const isDark=matchMedia('(prefers-color-scheme:dark)').matches;
  const gc=isDark?'rgba(255,255,255,0.07)':'rgba(0,0,0,0.06)';
  const tc=isDark?'rgba(255,255,255,0.45)':'rgba(0,0,0,0.4)';
  const datasets=dates.map((d,i)=>{{
    const s=a.series[d];
    return{{label:d+(s.flat?' ⚠':''),
      data:s.he.map((h,j)=>({{x:h,y:s.soc_pct?s.soc_pct[j]:null}})),
      borderColor:s.flat?FLAT_C:PAL[i%PAL.length],
      _raw:s.hbsoc,
      backgroundColor:'transparent',
      borderWidth:s.flat?2.5:1.2,
      pointRadius:dates.length>60?0:1.5,
      pointHoverRadius:4,tension:0.2}};
  }});
  if(chartInst)chartInst.destroy();
  const ctx=document.getElementById('chart').getContext('2d');
  chartInst=new Chart(ctx,{{type:'line',data:{{datasets}},options:{{
    responsive:true,maintainAspectRatio:false,
    interaction:{{mode:'nearest',intersect:false}},
    plugins:{{
      legend:{{display:dates.length<=40,position:'right',
        labels:{{boxWidth:12,font:{{size:10}},color:tc}}}},
      tooltip:{{callbacks:{{
        title:items=>`${{items[0].dataset.label}} · HE${{items[0].parsed.x}}`,
        label:item=>`HBSOC: ${{item.parsed.y!=null?item.parsed.y.toFixed(1):'N/A'}}%`
      }}}},
      title:{{display:true,
        text:`${{a.asset_name}} — Hour Beginning Planned SOC (%)`,
        color:'#0D1B3E',font:{{size:13,weight:'bold'}}}}
    }},
    scales:{{
      x:{{type:'linear',min:1,max:24,
        ticks:{{stepSize:2,callback:v=>`HE${{v}}`,color:tc,font:{{size:10}}}},
        grid:{{color:gc}},
        title:{{display:true,text:'Hour Ending',color:tc,font:{{size:10}}}}}},
      y:{{min:0,max:105,
        ticks:{{callback:v=>v+'%',color:tc,font:{{size:10}}}},
        grid:{{color:gc}},
        title:{{display:true,text:'HBSOC (%)',color:tc,font:{{size:10}}}}}}
    }}
  }}}});
}}

renderList('all','');
document.getElementById('search').addEventListener('input',e=>renderList(curFilter,e.target.value));
document.getElementById('flat-only').addEventListener('change',renderChart);
</script>
</body>
</html>"""
    return html

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  ERCOT ESR HBSOC Dashboard Builder")
    print("=" * 60)

    if not os.path.exists(RAW_CSV):
        print(f"\n⚠  {RAW_CSV} not found. Run ercot_esr_fetch.py first.")
        return

    df = load_and_process()
    summary = build_summary(df)

    total_esrs    = df["resource"].nunique()
    flat_esr_count= sum(1 for a in summary if a["flat_count"] > 0)
    flat_day_total= sum(a["flat_count"] for a in summary)

    print("\nWriting HTML dashboard ...")
    html = build_html(summary, total_esrs, flat_esr_count, flat_day_total)

    with open(OUTPUT_HTML, "w") as f:
        f.write(html)

    size_mb = os.path.getsize(OUTPUT_HTML) / 1024 / 1024
    print(f"  Saved: {OUTPUT_HTML} ({size_mb:.1f} MB)")

    if size_mb > 30:
        print(f"  ⚠  File is {size_mb:.1f} MB — may be slow to load in browser.")
        print(f"     Consider filtering to a date range or subset of assets.")
    else:
        print(f"  ✓  File size looks good for browser use.")

    print(f"\nDone!")
    print(f"  {total_esrs} ESRs  |  {flat_esr_count} with flat days  |  {flat_day_total} flat day-instances")
    print(f"  Open {OUTPUT_HTML} in Chrome to explore.")

if __name__ == "__main__":
    main()
