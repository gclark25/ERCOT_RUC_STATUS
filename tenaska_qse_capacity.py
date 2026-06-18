"""
tenaska_qse_capacity.py
-----------------------
Queries the ERCOT Public API for all generation resources managed by
Tenaska Power Services Co (QSE code: QTENSK) and reports total MW capacity.

Credentials are read from environment variables — set these as GitHub
Actions Secrets (Settings → Secrets and variables → Actions):

  ERCOT_USERNAME   your ERCOT Public API login email
  ERCOT_PASSWORD   your ERCOT Public API password
  ERCOT_SUB_KEY    your Ocp-Apim-Subscription-Key

Optional env vars (override defaults):
  QSE_NAME   full registered name filter  (default: TENASKA POWER SERVICES CO)
  QSE_CODE   short QSE code filter        (default: QTENSK)

Endpoints used:
  Auth:  https://ercotb2c.b2clogin.com/.../oauth2/v2.0/token  (ROPC flow)
  Data:  /np1-301/60_cop_adj_period_snapshot
         (COP snapshot — QSE name + HSL/LSL per resource, updated every ~15 min)
"""

import os
import sys
import requests
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

# ─── CREDENTIALS (from GitHub Actions Secrets / env vars) ────────────────────
ERCOT_USERNAME = os.getenv("ERCOT_USERNAME", "")
ERCOT_PASSWORD = os.getenv("ERCOT_PASSWORD", "")
ERCOT_SUB_KEY  = os.getenv("ERCOT_SUB_KEY",  "")

# ─── TARGET QSE — exact ERCOT registered identifiers ─────────────────────────
QSE_NAME = os.getenv("QSE_NAME", "TENASKA POWER SERVICES CO").upper().strip()
QSE_CODE = os.getenv("QSE_CODE", "QTENSK").upper().strip()

# ─── ERCOT API CONFIG ─────────────────────────────────────────────────────────
BASE_URL  = "https://api.ercot.com/api/public-reports"
TOKEN_URL = (
    "https://ercotb2c.b2clogin.com/ercotb2c.onmicrosoft.com"
    "/B2C_1_PUBAPI-ROPC-FLOW/oauth2/v2.0/token"
)
CLIENT_ID = "fec253ea-0d06-4272-a5e6-b478babc7532"  # ERCOT public client ID
SCOPE     = f"openid {CLIENT_ID} offline_access"

OUTPUT_DIR = Path("output")


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


# ─── FILTER HELPER ────────────────────────────────────────────────────────────
def filter_for_qse(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """
    Try to match Tenaska by QSE code first (most precise), then by name.
    Returns (filtered_df, matched_column_name).
    """
    df.columns = [c.lower() for c in df.columns]

    # Look for a QSE code column (e.g. 'qsecode', 'qse_code', 'qse')
    code_col = next(
        (c for c in df.columns if c in ("qsecode", "qse_code", "qse")), None
    )
    # Look for a QSE name column
    name_col = next(
        (c for c in df.columns if "qse" in c and "name" in c),
        next((c for c in df.columns if "qse" in c and c != code_col), None)
    )

    # Try exact code match first
    if code_col:
        mask = df[code_col].str.upper().str.strip() == QSE_CODE
        if mask.any():
            print(f"  Matched on QSE code column '{code_col}' = '{QSE_CODE}'")
            return df[mask].copy(), code_col

    # Fall back to name substring match
    if name_col:
        mask = df[name_col].str.upper().str.contains(QSE_NAME, na=False)
        if mask.any():
            print(f"  Matched on QSE name column '{name_col}' contains '{QSE_NAME}'")
            return df[mask].copy(), name_col

    # Nothing matched — return empty with whichever column we found
    match_col = name_col or code_col
    return df.iloc[0:0].copy(), match_col or ""


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
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
    print(f"  Target QSE:  {QSE_NAME}  [{QSE_CODE}]")
    print(f"  Run Time:    {run_ts}")
    print(f"{'='*60}\n")

    token = get_token()

    # ── Primary: NP1-301 COP Snapshot ─────────────────────────────────────────
    print("Querying NP1-301 COP Adjustment Period Snapshot...")
    endpoint = "/np1-301/60_cop_adj_period_snapshot"

    # Try with server-side QSE code filter first
    rows = []
    for attempt_params in [{"qse": QSE_CODE}, {"qseName": QSE_NAME}, {}]:
        label = f"params={attempt_params}" if attempt_params else "no filter (fetch all)"
        print(f"  Attempting with {label}...")
        try:
            rows = fetch_all_pages(endpoint, token, attempt_params)
            if rows:
                break
        except requests.HTTPError as e:
            print(f"  HTTP error: {e}")

    if not rows:
        print(
            "\nNo data returned from NP1-301.\n"
            "Possible causes:\n"
            "  • Endpoint requires a deliveryDate param — check https://apiexplorer.ercot.com/\n"
            "  • Your subscription tier doesn't include this report\n"
        )
        sys.exit(1)

    df = pd.DataFrame(rows)
    print(f"\nTotal records fetched: {len(df):,}")
    print(f"Raw columns: {list(df.columns)}\n")

    # ── Match Tenaska records ─────────────────────────────────────────────────
    qse_df, match_col = filter_for_qse(df)

    if qse_df.empty:
        print(f"\nNo resources found for QSE '{QSE_NAME}' / '{QSE_CODE}'.")
        print("QSE values present in dataset:")
        # Print all unique QSE-like column values to help diagnose
        df.columns = [c.lower() for c in df.columns]
        for col in df.columns:
            if "qse" in col:
                print(f"  [{col}]: {sorted(df[col].dropna().unique())[:30]}")
        save_csv(
            df[[c for c in df.columns if "qse" in c]].drop_duplicates(),
            "all_qse_values.csv"
        )
        sys.exit(0)

    print(f"Resources matched: {len(qse_df)}")

    # ── Identify MW columns ───────────────────────────────────────────────────
    hsl_col = next((c for c in qse_df.columns if "hsl" in c), None)
    lsl_col = next((c for c in qse_df.columns if "lsl" in c), None)
    res_col = next(
        (c for c in qse_df.columns if "resource" in c and "name" in c),
        next((c for c in qse_df.columns if "resource" in c), None)
    )

    if hsl_col:
        qse_df[hsl_col] = pd.to_numeric(qse_df[hsl_col], errors="coerce")
    if lsl_col:
        qse_df[lsl_col] = pd.to_numeric(qse_df[lsl_col], errors="coerce")

    total_hsl = qse_df[hsl_col].sum() if hsl_col else None
    total_lsl = qse_df[lsl_col].sum() if lsl_col else None

    # ── Print summary ─────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  QSE:              {QSE_NAME}  [{QSE_CODE}]")
    print(f"  Resources Found:  {len(qse_df)}")
    if total_hsl is not None:
        print(f"  Total HSL (MW):   {total_hsl:,.1f}")
    if total_lsl is not None:
        print(f"  Total LSL (MW):   {total_lsl:,.1f}")
    print(f"{'='*60}")

    # Resource-level detail
    detail_cols = [c for c in [match_col, res_col, hsl_col, lsl_col] if c]
    print(f"\nResource detail ({len(qse_df)} rows):")
    print(qse_df[detail_cols].sort_values(res_col or detail_cols[0]).to_string(index=False))

    # ── Save CSVs ─────────────────────────────────────────────────────────────
    print("\nSaving output files...")
    date_slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    save_csv(qse_df, f"tenaska_resources_{date_slug}.csv")

    print(f"\nDone. Run completed at {run_ts}")


if __name__ == "__main__":
    main()
