# ERCOT QSE Capacity Query

Queries the ERCOT Public API for all generation resources managed by a target QSE
(default: **TENASKA**) and reports total MW capacity (HSL/LSL) with a per-entity breakdown.

---

## Setup (one-time)

### 1. Add your ERCOT credentials as GitHub Secrets

Go to your repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these three secrets:

| Secret name | Value |
|---|---|
| `ERCOT_USERNAME` | Your ERCOT Public API login email |
| `ERCOT_PASSWORD` | Your ERCOT Public API password |
| `ERCOT_SUB_KEY` | Your `Ocp-Apim-Subscription-Key` from the ERCOT API portal |

> Your subscription key is found at [apiexplorer.ercot.com](https://apiexplorer.ercot.com/) under your profile.

### 2. That's it — no local setup needed

---

## Running the query

1. Go to the **Actions** tab in your repo
2. Click **Tenaska QSE Capacity Query** in the left sidebar
3. Click **Run workflow** (top right of the workflow table)
4. Optionally change the **QSE name search term** (default: `TENASKA`)
5. Click the green **Run workflow** button

The job takes ~30 seconds. When it finishes:

- Results are printed in the **Actions run log** (click the job → expand the "Run" step)
- CSV files are saved as a downloadable **artifact** (`tenaska-qse-results`) attached to the run
  - `tenaska_resources_YYYYMMDD_HHMM.csv` — one row per generation resource
  - `tenaska_by_qse_YYYYMMDD_HHMM.csv` — rollup by QSE entity

---

## What the script does

**Endpoint:** `NP1-301 / 60_cop_adj_period_snapshot`
(Current Operating Plan snapshot — updated every ~15 minutes, contains QSE name + HSL + LSL per resource)

**Fields reported:**
- **HSL** (High Sustained Limit) — effective maximum MW output, closest to dispatchable capacity
- **LSL** (Low Sustained Limit) — minimum stable MW output

**Auth:** ERCOT ROPC OAuth2 flow via `ercotb2c.b2clogin.com`

---

## Customizing the QSE search

To query a different QSE, change the search term at runtime in the **Run workflow** dialog,
or edit `QSE_SEARCH_TERM` at the top of `tenaska_qse_capacity.py`.

The search is a case-insensitive substring match, so `TENASKA` will match
`TENASKA POWER SERVICES`, `TENASKA TEXAS`, etc.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `401 Unauthorized` | Wrong credentials | Double-check secrets in repo Settings |
| `No data returned` | Endpoint needs date params | ERCOT may have changed the schema; check [apiexplorer.ercot.com](https://apiexplorer.ercot.com/) |
| `No resources found` | QSE name mismatch | Script prints all QSE names found — check the log and adjust search term |
| `KeyError` on column | ERCOT schema change | Check the `Raw columns:` line in the log and update column detection in script |
