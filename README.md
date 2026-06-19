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
| `data/raw_events.csv` | Sample generated test data (committed so reviewers can skip generation). |
| `data/processed_transactions.csv` | Sample pipeline output. |

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
  ATV decline (%) x recent transaction volume. It's meant to prioritize
  investigation, not as a precise revenue-recovery estimate.
- Test data is synthetic and seeded (`random.seed(42)`) for reproducibility;
  it does not need to be perfectly realistic, only complex enough to exercise
  the pipeline (per the challenge spec).

## Findings: What is causing Titan's revenue drop, and what should they do about it?

The drop in total transaction value is driven almost entirely by **falling
ATV, not falling transaction counts** — and the ATV decline (~27-28%) is
remarkably consistent across *every* corridor in the most recent 30 days
versus the prior 30 days, meaning this is a platform-wide trust/friction
problem introduced by the Q4 2024 Yuno migration, not a single bad corridor.
The clearest signal is that three corridors — **USA→Philippines,
UK→Nigeria, and Canada→India** — show drop-off rates of 23-29% (versus 8-11%
for the healthiest corridors) and failure rates 2-3x higher on **cash pickup
and mobile wallet** than on bank transfer in those same lanes. Mobile wallet
also has the lowest ATV of any payout method ($181.75 vs $265.45 for bank
transfer), consistent with senders sending smaller, lower-risk amounts
through the routes they trust least. The combined pattern — high friction in
specific corridor/payout-method combinations correlating with both higher
failure rates and smaller transfer sizes — strongly suggests senders are
reacting rationally to unreliable routing by sending test amounts or
splitting transfers, rather than abandoning the platform outright (counts
stayed flat). **Recommendation:** prioritize re-tuning Yuno's orchestration
logic for cash pickup and mobile wallet payouts in the USA→Philippines,
UK→Nigeria, and Canada→India corridors first (per `analytics.py`'s top-3
problem-area ranking, these combinations carry the highest estimated revenue
risk), since fixing the failure rate there should restore sender confidence
and let transfer sizes recover without needing to touch the otherwise-healthy
corridors.
