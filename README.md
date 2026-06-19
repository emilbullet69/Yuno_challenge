# Titan Remittance — Flow Analytics Pipeline

A proof-of-concept data pipeline and dashboard diagnosing Titan Remittance's
18% month-over-month drop in average transaction value (ATV) across its
cross-border payout corridors.

## What's in here

| File | Purpose |
|---|---|
| `generate_data.py` | Generates a synthetic raw transaction event log (~52k events, 75 days, 6 corridors x 3 payout methods, with injected data quality issues). |
| `transaction_etl.py` | Ingestion + cleaning + enrichment pipeline. Reads `data/raw_events.csv`, dedupes/cleans, enriches, writes `data/processed_transactions.csv`. |
| `analytics.py` | Computes the business KPIs (drop-off rate, ATV by payout method, failure rates, 30-vs-30 ATV trend, top problem areas). Runnable standalone for a text report. |
| `titan_dashboard.py` | Interactive Streamlit dashboard — filters, charts, ranked tables, plain-language summary for a non-technical stakeholder. |
| `generate_screenshots.py` | Exports the dashboard's charts/tables to static PNGs in `screenshots/`, so a reviewer can see sample output without running Streamlit. |
| `FINDINGS.md` | Standalone written summary answering "what is causing the revenue drop and what should Titan do about it." |
| `data/raw_events.csv` | Sample generated test data (committed so reviewers can skip generation). |
| `data/processed_transactions.csv` | Sample pipeline output. |
| `screenshots/` | Sample chart/table output (PNG), committed for reviewers who don't want to run the dashboard. |

## How to run

```bash
pip install pandas numpy streamlit plotly

# 1. Generate synthetic raw event data (optional — a sample is already committed)
python3 generate_data.py

# 2. Run the ETL pipeline (ingest, clean, enrich)
python3 transaction_etl.py

# 3a. Text-only KPI report
python3 analytics.py

# 3b. Interactive dashboard (recommended)
streamlit run titan_dashboard.py
```

Each script can be run independently and fails with a clear, actionable error
message (not a stack trace) if a prior step hasn't been run or its output is
missing/corrupt.

## Pipeline design notes

**Performance:** `transaction_etl.py` processes ~52,000 raw events (after
dedup, ~52,000 -> ~52,000 clean rows) in under 5 seconds on a single core,
using vectorized pandas operations throughout (no row-by-row `.apply` in the
hot path) — well inside the 50k-rows/<10s target.

**Data quality issues handled (and deliberately injected into the test data):**
- **Duplicate transaction IDs** — kept the most-advanced/most-recent status per
  transaction id rather than first/last-seen, since a duplicate event is
  usually a late-arriving status update, not a literal copy.
- **Missing timestamps** — parsed with `errors="coerce"` so a malformed/blank
  timestamp becomes `NaT` instead of crashing the load; rows with missing
  `initiated_at` are excluded from date-based filtering/trend charts but kept
  in the dataset.
- **Out-of-order timestamps** (e.g. `completed_at` before `authorized_at`) —
  detected and flagged via `timestamps_consistent`; excluded from
  time-to-completion math so a single bad event can't produce a negative
  duration or skew the average.
- **Invalid/missing amounts** — non-numeric, negative, or absurdly large
  `amount_usd` values are flagged via `amount_is_valid` and excluded from all
  amount-based aggregates (ATV, trend, problem-area ranking) instead of
  silently corrupting them.
- **Unknown/blank categorical fields** — corridor, payout method, and status
  values that are missing/blank are bucketed as `"unknown"` so group-bys and
  dashboard filters never throw on an unexpected category.
- **Empty inputs/outputs** — every script checks for missing files, empty
  files, missing required columns, and an empty dataframe after cleaning, and
  exits with a clear message rather than producing a misleading empty report.

## Assumptions

- "Corridor" = `send_country -> receive_country` (currency pair and payout
  method are tracked as separate dimensions, not folded into the corridor id).
- A transaction that doesn't reach `"completed"` is treated as "dropped off"
  for drop-off-rate purposes, whether it explicitly failed or is still
  in-progress in the log.
- The 30-vs-30 ATV trend compares the most recent 30 days to the prior 30
  days, anchored to the latest `initiated_at` timestamp in the (filtered)
  dataset — not to today's wall-clock date — so it works correctly on
  historical or filtered data.
- "Revenue risk" ranking for top problem areas is a simple heuristic: size of
  ATV decline (%) x total initiated volume x (1 + failure rate). It's meant
  to prioritize investigation, not as a precise revenue-recovery estimate.
- Test data is synthetic and seeded (`random.seed(42)`) for reproducibility;
  it does not need to be perfectly realistic, only complex enough to exercise
  the pipeline (per the challenge spec).

## Stretch goals covered

- **Cost-benefit analysis** — `top_problem_areas()` in `analytics.py`
  estimates `est_recoverable_monthly_usd` per corridor/payout-method
  combination: if that lane's ATV recovered to its prior-30-day level, how
  much extra transaction volume would its last-30-day transaction count
  have produced. Surfaced in both the text report and the dashboard table.
- **Interactive filtering** — the Streamlit dashboard filters by corridor,
  payout method, status, date range, and amount range, all recomputing the
  KPIs and charts live.
- **Anomaly/degradation flagging (partial)** — the 30-vs-30 ATV trend and
  the risk-weighted problem-area ranking both compare recent performance to
  historical, surfacing corridors/methods whose ATV or failure rate has
  degraded. This isn't a full statistical anomaly detector (e.g. no z-scores
  or rolling-window baselines), just a direct period-over-period comparison.
- **Not attempted: user behavior segmentation** — the test data has no
  sender/user identifier, so first-time-vs-repeat-sender analysis isn't
  possible without changing the data model; out of scope given the time box.

## Findings: What is causing Titan's revenue drop, and what should they do about it?

See [`FINDINGS.md`](FINDINGS.md) for the full write-up. Short version: ATV
fell ~27-28% across *every* corridor (a platform-wide trust/friction issue
from the Q4 2024 Yuno migration, not one bad lane), and the top revenue-risk
combinations — ranked by ATV decline x transaction volume x failure rate —
are **USA→Philippines cash pickup, UK→Nigeria cash pickup, and USA→Philippines
mobile wallet**, representing an estimated $140K+/month in recoverable
volume if fixed.

Sample chart/table output (generated via `generate_screenshots.py`) is in
[`screenshots/`](screenshots/); the same views are available live and
filterable in the Streamlit dashboard.
