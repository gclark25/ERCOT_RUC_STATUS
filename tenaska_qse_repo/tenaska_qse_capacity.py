"""
tenaska_qse_capacity.py
-----------------------
Queries the ERCOT Public API for all generation resources managed by
a QSE name search term (default: TENASKA) and reports total MW capacity.

Credentials are read from environment variables — set these as GitHub
Actions Secrets (Settings → Secrets and variables → Actions):

  ERCOT_USERNAME   your ERCOT Public API login email
  ERCOT_PASSWORD   your ERCOT Public API password
  ERCOT_SUB_KEY    your Ocp-Apim-Subscription-Key

Optional env var:
  QSE_SEARCH_TERM  override the QSE name filter (default: TENASKA)

Endpoints used:
  Auth:  https://ercotb2c.b2clogin.com/.../oauth2/v2.0/token  (ROPC flow)
  Data:  /np1-301/60_cop_adj_period_snapshot
         (COP snapshot — QSE name + HSL/LSL per resource, updated every ~15 min)
"""

import os
import sys
import csv
import requests
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

# ─── CREDENTIALS & CONFIG (all from environment) ──────────────────────────────
ERCOT_USERNAME  = os.getenv("ERCOT_USERNAME",  "")
ERCOT_PASSWORD  = os.getenv("ERCOT_PASSWORD",  "")
ERCOT_SUB_KEY   = os.getenv("ERCOT_SUB_KEY",   "")
QSE_SEARCH_TERM = os.getenv("QSE_SEARCH_TERM", "TENASKA").upper()

BASE_URL        = "https://api.ercot.com/api/public-reports"
TOKEN_URL       = (
    "https://ercotb2c.b2clogin.com/ercotb2c.onmicrosoft.com"
    "/B2C_1_PUBAPI-ROPC-FLOW/oauth2/v2.0/token"
)
CLIENT_ID       = "fec253ea-0d06-4272-a5e6-b478babc7532"  # ERCOT public client ID
SCOPE           = f"openid {CLIENT_ID} offline_access"

OUTPUT_DIR      = Path("output")


# ─── AUTH ─────────────────────────────────────────────────────────────────────
def get_token() -> str:
    """Fetch an OAuth2 bearer token via ROPC flow."""
    resp = requests.post(TOKEN_URL, data={
        "grant_type":    "password",
        "username":      ERCOT_USERNAME,
        "password":      ERCOT_PASSWORD,
        "scope":         SCOPE,
        "client_id":     CLIENT_ID,
        "response_type": "id_token",
    })
    resp.raise_for_status()
    token = resp.json().get("access_token") or resp.json().get("id_token")
    if not token:
        raise ValueError(f"No token in auth response: {list(resp.json().keys())}")
    print("✓ Authenticated successfully")
    return token


# ─── PAGINATION HELPER ────────────────────────────────────────────────────────
def fetch_all_pages(endpoint: str, token: str, params: dict = None) -> list[dict]:
    """Pages through all results from an ERCOT API endpoint."""
    headers = {
        "Authorization":             f"Bearer {token}",
        "Ocp-Apim-Subscription-Key": ERCOT_SUB_KEY,
    }
    url      = f"{BASE_URL}{endpoint}"
    page     = 1
    all_rows = []
    params   = dict(params or {})

    while True:
        params["page"] = page
        resp = requests.get(url, headers=headers, params=params)
        if resp.status_code == 404:
            print(f"  404 on page {page} — no more data")
            break
        resp.raise_for_status()
        data = resp.json()

        # Unwrap ERCOT's nested response structure
        rows = []
        if isinstance(data, dict):
            inner = data.get("data", data)
            if isinstance(inner, dict):
                rows = inner.get("data", [])
            elif isinstance(inner, list):
                rows = inner
        elif isinstance(data, list):
            rows = data

        if not rows:
            break

        all_rows.extend(rows)

        meta        = data.get("_meta", {})
        total_pages = int(meta.get("totalPages") or meta.get("total_pages") or 1)
        print(f"  Page {page}/{total_pages} — {len(rows)} rows")

        if page >= total_pages:
            break
        page += 1

    return all_rows


# ─── OUTPUT ───────────────────────────────────────────────────────────────────
def save_csv(df: pd.DataFrame, filename: str) -> Path:
    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / filename
    df.to_csv(path, index=False)
    print(f"  Saved → {path}")
    return path


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    # Validate credentials
    if not all([ERCOT_USERNAME, ERCOT_PASSWORD, ERCOT_SUB_KEY]):
        print(
            "ERROR: Missing credentials. Set these GitHub Actions Secrets:\n"
            "  ERCOT_USERNAME, ERCOT_PASSWORD, ERCOT_SUB_KEY\n\n"
            "Go to: your repo → Settings → Secrets and variables → Actions → New repository secret"
        )
        sys.exit(1)

    run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n{'='*60}")
    print(f"  ERCOT QSE Capacity Query")
    print(f"  QSE Filter:  {QSE_SEARCH_TERM}")
    print(f"  Run Time:    {run_ts}")
    print(f"{'='*60}\n")

    token = get_token()

    # ── Primary: NP1-301 COP Snapshot ─────────────────────────────────────────
    print("Querying NP1-301 COP Adjustment Period Snapshot...")
    endpoint = "/np1-301/60_cop_adj_period_snapshot"
    params   = {"qse": QSE_SEARCH_TERM}

    try:
        rows = fetch_all_pages(endpoint, token, params)
    except requests.HTTPError as e:
        print(f"  HTTP error: {e}")
        rows = []

    # ── Fallback: remove server-side QSE filter and filter locally ────────────
    if not rows:
        print("  No results with QSE filter — retrying without server-side filter...")
        try:
            rows = fetch_all_pages(endpoint, token, {})
        except requests.HTTPError as e:
            print(f"  HTTP error on retry: {e}")
            rows = []

    if not rows:
        print(
            "\nNo data returned from NP1-301.\n"
            "Possible causes:\n"
            "  • Endpoint requires a deliveryDate param — ERCOT may have changed the schema\n"
            "  • Your subscription tier doesn't include this report\n"
            "  • Try the ERCOT API Explorer at https://apiexplorer.ercot.com/ to confirm\n"
            "    the correct parameters for /np1-301/60_cop_adj_period_snapshot\n"
        )
        sys.exit(1)

    df = pd.DataFrame(rows)
    df.columns = [c.lower() for c in df.columns]
    print(f"\nTotal records fetched: {len(df):,}")
    print(f"Columns: {list(df.columns)}\n")

    # ── Identify key columns ──────────────────────────────────────────────────
    qse_col = next(
        (c for c in df.columns if "qse" in c and "name" in c),
        next((c for c in df.columns if "qse" in c), None)
    )
    hsl_col = next((c for c in df.columns if "hsl" in c), None)
    lsl_col = next((c for c in df.columns if "lsl" in c), None)
    res_col = next(
        (c for c in df.columns if "resource" in c and "name" in c),
        next((c for c in df.columns if "resource" in c), None)
    )

    if not qse_col:
        print(f"WARNING: No QSE column found. Available columns:\n{list(df.columns)}")
        save_csv(df, "raw_cop_snapshot.csv")
        sys.exit(1)

    # ── Filter for target QSE ─────────────────────────────────────────────────
    tenaska_df = df[df[qse_col].str.upper().str.contains(QSE_SEARCH_TERM, na=False)].copy()
    print(f"Resources matching '{QSE_SEARCH_TERM}': {len(tenaska_df)}")

    if tenaska_df.empty:
        print(f"\nNo resources found for '{QSE_SEARCH_TERM}'.")
        print("Sample QSE names in dataset:")
        print(df[qse_col].dropna().unique()[:20])
        save_csv(df[[qse_col]].drop_duplicates().sort_values(qse_col), "all_qse_names.csv")
        sys.exit(0)

    # ── Compute summary ───────────────────────────────────────────────────────
    if hsl_col:
        tenaska_df[hsl_col] = pd.to_numeric(tenaska_df[hsl_col], errors="coerce")
    if lsl_col:
        tenaska_df[lsl_col] = pd.to_numeric(tenaska_df[lsl_col], errors="coerce")

    total_hsl = tenaska_df[hsl_col].sum() if hsl_col else None
    total_lsl = tenaska_df[lsl_col].sum() if lsl_col else None
    count     = len(tenaska_df)

    # ── Print results ─────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  QSE Filter:       {QSE_SEARCH_TERM}")
    print(f"  Resources Found:  {count}")
    if total_hsl is not None:
        print(f"  Total HSL (MW):   {total_hsl:,.1f}")
    if total_lsl is not None:
        print(f"  Total LSL (MW):   {total_lsl:,.1f}")
    print(f"{'='*60}")

    # Breakdown by QSE entity (catches multiple Tenaska registrations)
    if hsl_col:
        print("\nBreakdown by QSE entity:")
        breakdown = (
            tenaska_df.groupby(qse_col)[hsl_col]
            .agg(resource_count="count", total_hsl_mw="sum")
            .sort_values("total_hsl_mw", ascending=False)
        )
        print(breakdown.to_string())

    # Resource-level detail table
    detail_cols = [c for c in [qse_col, res_col, hsl_col, lsl_col] if c]
    print(f"\nResource detail ({len(tenaska_df)} rows):")
    print(tenaska_df[detail_cols].sort_values(qse_col).to_string(index=False))

    # ── Save outputs ──────────────────────────────────────────────────────────
    print("\nSaving output files...")
    date_slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    save_csv(tenaska_df, f"tenaska_resources_{date_slug}.csv")
    if hsl_col:
        save_csv(breakdown.reset_index(), f"tenaska_by_qse_{date_slug}.csv")

    print(f"\nDone. Run completed at {run_ts}")


if __name__ == "__main__":
    main()
