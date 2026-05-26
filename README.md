# ERCOT ONRUC Monitor

A Python script to pull all resources committed via Reliability Unit Commitment (RUC) from the ERCOT Public API, using the 60-Day COP Adjustment Period Snapshot (NP1-301). Isolates units with `operatingMode == ONRUC` and captures their High Sustained Limit (HSL) for each committed hour.

---

## Background

ERCOT posts Current Operating Plans (COPs) for all generation resources on a **60-day lag** via its public API. Each record contains the resource's operating mode for a given hour — including `ONRUC`, which indicates the unit was administratively committed by ERCOT rather than dispatched through normal competitive market mechanisms.

When a unit is RUC-committed:
- It is required to run and is paid at cost, not at the market clearing price
- Its capacity is removed from the marginal stack
- The scarcity price signal for that hour is suppressed

Tracking `ONRUC` frequency, duration, and HSL over time provides a quantitative basis for analyzing ERCOT's reliance on administrative commitment and its impact on price formation.

---

## Data Source

| Field | Value |
|---|---|
| API | [ERCOT Public Data API](https://developer.ercot.com/applications/pubapi/user-guide/using-api/) |
| Product | NP1-301 — 60-Day COP Adjustment Period Snapshot |
| Endpoint | `/np1-301/60_cop_adj_period_snapshot` |
| Base URL | `https://api.ercot.com/api/public-reports` |
| Posting lag | 60 days (e.g. data through March is available in late May) |
| Coverage | January 1, 2025 → present (rolling as data is posted) |

---

## Output

Running the script produces `ercot_onruc_2025_present.csv` with the following columns:

| Column | Description |
|---|---|
| `resource` | ERCOT resource name (unit identifier) |
| `operatingdate` | The date the unit was operating |
| `hourending` | The hour ending (1–24, CPT) |
| `hsl` | High Sustained Limit in MW — the unit's declared operating capacity |
| `operatingmode` | Will always be `ONRUC` in this output |
| `postdatetime` | When ERCOT posted the COP record |

---

## Setup

### 1. Register for ERCOT API access

Registration is free and requires only email verification.

1. Go to [https://apiexplorer.ercot.com/](https://apiexplorer.ercot.com/)
2. Click **Sign In / Sign Up** and create an account
3. Navigate to the **Products** page and subscribe to the Public API product
4. Copy your **Primary Key** from your profile — this is your subscription key

### 2. Install dependencies

```bash
pip install requests pandas
```

### 3. Set credentials

Export your credentials as environment variables before running:

```bash
export ERCOT_USERNAME="your@email.com"
export ERCOT_PASSWORD="yourpassword"
export ERCOT_SUBSCRIPTION_KEY="yoursubscriptionkey"
```

Alternatively, set them directly in the script at the top of `ercot_onruc_pull.py`.

---

## Usage

```bash
python ercot_onruc_pull.py
```

The script will:
1. Authenticate against the ERCOT OAuth endpoint (token valid 1 hour)
2. Paginate through all COP records from `2025-01-01` to present
3. Filter in-flight to `operatingMode == ONRUC`
4. Save results to `ercot_onruc_2025_present.csv`
5. Print a summary of top RUC-committed resources and daily record counts

---

## Notes

**On the 60-day lag:** ERCOT posts COP data 60 days after the operating date. Re-running the script monthly will extend coverage as new data becomes available.

**On pagination:** The script pages at 10,000 records per request with a 0.3-second delay between pages. The full pre-filter dataset across all resources and hours is large; runtime will vary depending on the date range.

**On token expiration:** ID tokens expire after 1 hour. For very large pulls that may exceed this window, the script can be extended to re-authenticate mid-run — see the ERCOT auth documentation linked below.

**On column names:** ERCOT occasionally adjusts field names across API versions. The script tries multiple common variants for each target column and will warn if a field cannot be matched.

---

## References

- [ERCOT Public API Registration & Authentication](https://developer.ercot.com/applications/pubapi/user-guide/registration-and-authentication/)
- [ERCOT Public API Usage Guide](https://developer.ercot.com/applications/pubapi/user-guide/using-api/)
- [NP1-301 Data Product Archive](https://data.ercot.com/data-product-archive/NP1-301)
- [ERCOT Nodal Protocols — Resource Status Codes](https://www.ercot.com/mktrules/nprotocols/current)
