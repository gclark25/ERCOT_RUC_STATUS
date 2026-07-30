"""
ERCOT ESR HBSOC — Fetch Only
==============================
Step 1 of 2. Pulls hourBeginningPlannedSOC for all ESRs from the ERCOT
NP1-301 COP snapshot and streams rows directly to CSV as they arrive.
No processing, no dashboard, no growing memory problem.

Run Step 2 (ercot_esr_build.py) after this completes to build the dashboard.

USAGE
-----
    pip install requests
    python ercot_esr_fetch.py

OUTPUT
------
    ercot_esr_hbsoc_raw.csv   — one row per API record with hbsoc value
    ercot_esr_progress.txt    — last completed page (for resume)
"""

import os, time, csv, requests
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

START_DATE    = "2026-01-01"
END_DATE      = "2026-05-30"
PAGE_SIZE     = 10000
SLEEP_SEC     = 0.25
OUTPUT_CSV    = "ercot_esr_hbsoc_raw.csv"
PROGRESS_FILE = "ercot_esr_progress.txt"

# ── AUTH ──────────────────────────────────────────────────────────────────────
def get_token():
    resp = requests.post(AUTH_URL, data={
        "username": USERNAME, "password": PASSWORD,
        "grant_type": "password", "scope": SCOPE,
        "client_id": CLIENT_ID, "response_type": "id_token",
    }, timeout=30)
    resp.raise_for_status()
    token = resp.json().get("id_token")
    if not token: raise ValueError("Auth failed — no id_token")
    return token

def make_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Ocp-Apim-Subscription-Key": SUBSCRIPTION_KEY,
        "Accept": "application/json",
    }

# ── FETCH ─────────────────────────────────────────────────────────────────────
def fetch_page(hdrs, page, retries=5):
    for attempt in range(retries):
        try:
            resp = requests.get(f"{BASE_URL}{ENDPOINT}", headers=hdrs, params={
                "deliveryDateFrom": START_DATE, "deliveryDateTo": END_DATE,
                "size": PAGE_SIZE, "page": page,
            }, timeout=180)
            if resp.status_code == 401:
                raise PermissionError("401 Unauthorized — token expired")
            if resp.status_code == 503:
                if attempt < retries - 1:
                    wait = (attempt + 1) * 30
                    print(f"\n  503 on page {page}, waiting {wait}s before retry (attempt {attempt+2}/{retries})...", end=" ", flush=True)
                    time.sleep(wait)
                    continue
            resp.raise_for_status()
            return resp.json()
        except PermissionError:
            raise
        except Exception as e:
            if attempt < retries - 1:
                wait = (attempt + 1) * 15
                print(f"\n  Error: {e} — retrying in {wait}s...", end=" ", flush=True)
                time.sleep(wait)
            else:
                raise

# ── RESUME LOGIC ──────────────────────────────────────────────────────────────
def get_resume_page():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            try: return int(f.read().strip())
            except: pass
    return 1

def save_progress(page):
    with open(PROGRESS_FILE, "w") as f:
        f.write(str(page))

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("  ERCOT ESR HBSOC Fetch")
    print(f"  Date range: {START_DATE} → {END_DATE}")
    print("=" * 60)

    if "YOUR_EMAIL" in USERNAME:
        print("\n⚠  Set credentials as env vars and re-run.")
        return

    resume_page = get_resume_page()
    append_mode = resume_page > 1 and os.path.exists(OUTPUT_CSV)

    if append_mode:
        print(f"\nResuming from page {resume_page} — appending to existing {OUTPUT_CSV}")
    else:
        print(f"\nStarting fresh from page 1")
        resume_page = 1

    print("\nAuthenticating ...")
    token = get_token()
    hdrs  = make_headers(token)
    token_time = time.time()
    print("  Authenticated.")

    page   = resume_page
    total  = None
    fields = None
    hbsoc_idx = None
    rows_written = 0

    # Open CSV — append if resuming, write if fresh
    mode = "a" if append_mode else "w"
    with open(OUTPUT_CSV, mode, newline="") as csvfile:
        writer = None

        print(f"\nFetching: {START_DATE} → {END_DATE}\n")

        while True:
            # Re-authenticate if token is about to expire (55 min limit)
            if time.time() - token_time > 3300:
                print("\n  Re-authenticating (token near expiry)...")
                token = get_token()
                hdrs  = make_headers(token)
                token_time = time.time()
                print("  Done.")

            print(f"  Page {page}" + (f"/{-(-total//PAGE_SIZE)}" if total else "") + " ...",
                  end=" ", flush=True)

            try:
                data = fetch_page(hdrs, page)
            except PermissionError:
                print(f"\n  401 — token expired. Save progress and re-run.")
                save_progress(page)
                print(f"  Progress saved at page {page}. Just re-run the script.")
                break
            except Exception as e:
                print(f"\n  Page {page} failed after all retries: {e}")
                print(f"  Skipping page {page} and continuing...")
                save_progress(page + 1)
                page += 1
                time.sleep(5)
                continue

            meta = data.get("_meta", {})
            raw_fields = data.get("fields", [])
            rows = data.get("data", [])

            if raw_fields and isinstance(raw_fields[0], dict):
                raw_fields = [f["name"] for f in raw_fields]

            if total is None:
                total = meta.get("totalRecords", 0)
                fields = raw_fields
                hbsoc_idx = fields.index("hourBeginningPlannedSOC") if "hourBeginningPlannedSOC" in fields else None
                print(f"Total: {total:,} records | hbsoc idx: {hbsoc_idx}")

                # Write header only on fresh start
                if not append_mode:
                    # Only write the columns we care about
                    out_cols = ["deliveryDate","qseName","resourceName","hourEnding",
                                "highSustainedLimit","hourBeginningPlannedSOC"]
                    writer = csv.DictWriter(csvfile, fieldnames=out_cols, extrasaction="ignore")
                    writer.writeheader()
                else:
                    out_cols = ["deliveryDate","qseName","resourceName","hourEnding",
                                "highSustainedLimit","hourBeginningPlannedSOC"]
                    writer = csv.DictWriter(csvfile, fieldnames=out_cols, extrasaction="ignore")

            if not rows:
                print("  No more rows.")
                save_progress(1)  # reset for next fresh run
                if os.path.exists(PROGRESS_FILE):
                    os.remove(PROGRESS_FILE)
                break

            kept = 0
            for row in rows:
                if hbsoc_idx is not None and row[hbsoc_idx] is not None and row[hbsoc_idx] != 0:
                    record = dict(zip(fields, row))
                    writer.writerow(record)
                    kept += 1

            rows_written += kept
            print(f"  {len(rows):,} rows, {kept} ESR kept (total written: {rows_written:,})", flush=True)
            save_progress(page)

            if page * PAGE_SIZE >= total or len(rows) < PAGE_SIZE:
                print(f"\n  Complete! {rows_written:,} ESR rows written to {OUTPUT_CSV}")
                if os.path.exists(PROGRESS_FILE):
                    os.remove(PROGRESS_FILE)
                break

            page += 1
            time.sleep(SLEEP_SEC)

    print(f"\nDone. Run ercot_esr_build.py to build the dashboard.")

if __name__ == "__main__":
    main()
