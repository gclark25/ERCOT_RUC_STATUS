"""
ERCOT COP ONRUC Status Pull
============================
Pulls all resource hours with status ONRUC from the ERCOT Public API
using the NP1-301 (60-Day COP Adjustment Period Snapshot) endpoint.
Captures: resource name, operating date, hour ending, HSL, and post datetime.

SETUP
-----
1. Register at https://apiexplorer.ercot.com/ (free, email-verified)
2. Subscribe to a product on the Products page to get your subscription key
3. Set the three credentials below (or export as env variables)

USAGE
-----
    pip install requests pandas
    python ercot_onruc_pull.py

OUTPUT
------
    ercot_onruc_2025_present.csv  — all ONRUC records since 2025-01-01
"""

import os
import time
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone

# ─── CREDENTIALS ──────────────────────────────────────────────────────────────
# Set these directly or export as environment variables:
#   export ERCOT_USERNAME="your@email.com"
#   export ERCOT_PASSWORD="yourpassword"
#   export ERCOT_SUBSCRIPTION_KEY="yoursubscriptionkey"

USERNAME         = os.getenv("ERCOT_USERNAME",         "YOUR_EMAIL")
PASSWORD         = os.getenv("ERCOT_PASSWORD",         "YOUR_PASSWORD")
SUBSCRIPTION_KEY = os.getenv("ERCOT_SUBSCRIPTION_KEY", "YOUR_SUBSCRIPTION_KEY")

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BASE_URL      = "https://api.ercot.com/api/public-reports"
AUTH_URL      = (
    "https://ercotb2c.b2clogin.com/ercotb2c.onmicrosoft.com"
    "/B2C_1_PUBAPI-ROPC-FLOW/oauth2/v2.0/token"
)
# NP1-301: 60-Day COP Adjustment Period Snapshot — contains operatingMode + hsl
ENDPOINT      = "/np1-301/60_cop_adj_period_snapshot"
CLIENT_ID     = "fec253ea-0d06-4272-a5e6-b478baeecd70"
SCOPE         = f"openid {CLIENT_ID} offline_access"

START_DATE    = "2025-01-01"
# Data is on a 60-day lag; pull up to today and let the API return what's posted
END_DATE      = datetime.now(timezone.utc).strftime("%Y-%m-%d")

PAGE_SIZE     = 10000   # max rows per page
SLEEP_SECONDS = 0.3     # rate-limit courtesy delay between pages
OUTPUT_FILE   = "ercot_onruc_2025_present.csv"

# ─── AUTH ─────────────────────────────────────────────────────────────────────

def get_id_token():
    """Authenticate and return the id_token (valid 1 hour)."""
    params = {
        "username":      USERNAME,
        "password":      PASSWORD,
        "grant_type":    "password",
        "scope":         SCOPE,
        "client_id":     CLIENT_ID,
        "response_type": "id_token",
    }
    resp = requests.post(AUTH_URL, data=params, timeout=30)
    resp.raise_for_status()
    token = resp.json().get("id_token")
    if not token:
        raise ValueError(f"Auth failed — no id_token in response: {resp.text[:500]}")
    print("  Authenticated successfully.")
    return token


def make_headers(token):
    return {
        "Authorization":           f"Bearer {token}",
        "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY,
        "Accept":                  "application/json",
    }


# ─── FETCH ────────────────────────────────────────────────────────────────────

def fetch_page(headers, page: int, start: str, end: str) -> dict:
    """Fetch one page of COP snapshot data filtered to a date range."""
    url = f"{BASE_URL}{ENDPOINT}"
    params = {
        "deliveryDateFrom": start,
        "deliveryDateTo":   end,
        "size":             PAGE_SIZE,
        "page":             page,
    }
    resp = requests.get(url, headers=headers, params=params, timeout=60)
    if resp.status_code == 401:
        raise PermissionError("401 Unauthorized — check credentials and subscription key.")
    resp.raise_for_status()
    return resp.json()


def fetch_all_onruc(token: str) -> pd.DataFrame:
    """
    Paginate through all COP snapshot records from START_DATE to END_DATE,
    filter to operatingMode == 'ONRUC', and return a DataFrame.
    """
    headers  = make_headers(token)
    all_rows = []
    page     = 1
    total    = None

    print(f"\nFetching COP snapshot: {START_DATE} → {END_DATE}")
    print("Filtering to ONRUC status only ...\n")

    while True:
        print(f"  Page {page}" + (f" / {-(-total // PAGE_SIZE)}" if total else "") + " ...", end=" ")
        data = fetch_page(headers, page, START_DATE, END_DATE)

        # Response shape: { "data": [[col1, col2, ...], ...], "fields": [...], "_meta": {...} }
        meta   = data.get("_meta", {})
        fields = data.get("fields", [])
        rows   = data.get("data", [])

        if total is None:
            total = meta.get("totalRecords", 0)
            print(f"Total records in range: {total:,}")
            if fields:
                print(f"  Columns: {fields}")

        if not rows:
            print("  No more rows.")
            break

        # Convert to dicts
        for row in rows:
            record = dict(zip(fields, row))
            # Filter to ONRUC only (case-insensitive safety check)
            if str(record.get("operatingMode", "")).upper() == "ONRUC":
                all_rows.append(record)

        print(f"  → {len(rows):,} records on this page | ONRUC kept so far: {len(all_rows):,}")

        # Check if we've read all pages
        retrieved = page * PAGE_SIZE
        if retrieved >= total or len(rows) < PAGE_SIZE:
            break

        page += 1
        time.sleep(SLEEP_SECONDS)

    if not all_rows:
        print("\nNo ONRUC records found in the date range.")
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    return df


# ─── PROCESS ──────────────────────────────────────────────────────────────────

def clean_and_save(df: pd.DataFrame):
    """Select relevant columns, sort, and save to CSV."""

    # Normalise column names to lowercase for safe access
    df.columns = [c.lower() for c in df.columns]

    # Print all available columns so user can see what came back
    print(f"\nColumns returned by API: {list(df.columns)}")

    # Build output with the fields we care about.
    # Column names may vary slightly — map common variants.
    col_map = {
        "resource":        ["resource", "resourcename", "resource_name", "duns"],
        "operatingdate":   ["operatingdate", "operating_date", "deliverydate", "delivery_date"],
        "hourending":      ["hourending", "hour_ending", "deliveryhour", "delivery_hour", "intervalending"],
        "hsl":             ["hsl", "highsustainedlimit", "high_sustained_limit"],
        "operatingmode":   ["operatingmode", "operating_mode", "resourcestatus", "resource_status"],
        "postdatetime":    ["postdatetime", "post_datetime", "postdate", "post_date"],
    }

    out = {}
    for target, candidates in col_map.items():
        for c in candidates:
            if c in df.columns:
                out[target] = df[c]
                break
        if target not in out:
            print(f"  WARNING: could not find column for '{target}' — will be blank")
            out[target] = None

    result = pd.DataFrame(out)

    # Clean up types
    for col in ["hsl"]:
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce")

    # Sort by operating date, hour, then resource
    sort_cols = [c for c in ["operatingdate", "hourending", "resource"] if c in result.columns]
    if sort_cols:
        result.sort_values(sort_cols, inplace=True)

    result.to_csv(OUTPUT_FILE, index=False)
    print(f"\n{'='*60}")
    print(f"  Saved {len(result):,} ONRUC records to: {OUTPUT_FILE}")
    print(f"{'='*60}")

    # Print a quick summary
    if "resource" in result.columns:
        top = result["resource"].value_counts().head(15)
        print(f"\nTop 15 most-frequently RUC-committed resources (by record count):")
        print(top.to_string())

    if "operatingdate" in result.columns:
        by_date = result.groupby("operatingdate").size().rename("onruc_records")
        print(f"\nRecords per operating date (first 20):")
        print(by_date.head(20).to_string())

    return result


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  ERCOT COP ONRUC Pull")
    print(f"  Date range: {START_DATE} → {END_DATE}")
    print(f"  Note: data is on a 60-day posting lag")
    print("=" * 60)

    if "YOUR_EMAIL" in USERNAME or "YOUR_SUBSCRIPTION_KEY" in SUBSCRIPTION_KEY:
        print("\n⚠  CREDENTIALS NOT SET")
        print("   Edit this script and set USERNAME, PASSWORD, SUBSCRIPTION_KEY")
        print("   or export them as environment variables:")
        print("     export ERCOT_USERNAME='your@email.com'")
        print("     export ERCOT_PASSWORD='yourpassword'")
        print("     export ERCOT_SUBSCRIPTION_KEY='yourkey'")
        print("\n   Register at: https://apiexplorer.ercot.com/")
        return

    print("\nStep 1: Authenticating ...")
    token = get_id_token()

    print("\nStep 2: Pulling COP data ...")
    df = fetch_all_onruc(token)

    if df.empty:
        print("No data returned. Verify date range and that data is within the 60-day posting window.")
        return

    print("\nStep 3: Processing and saving ...")
    clean_and_save(df)


if __name__ == "__main__":
    main()
