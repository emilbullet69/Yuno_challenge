"""
Titan Remittance - Data Ingestion & Enrichment Pipeline.

Reads raw transaction event logs, cleans known data quality issues
(duplicates, missing timestamps, out-of-order timestamps), enriches each
record with analysis-ready fields (corridor, time-to-completion, success
flag, drop-off stage), and writes a single tidy fact table.

Corner cases handled:
  - Missing/empty input file, empty file, unreadable file (permissions).
  - Missing required columns in the CSV header.
  - Duplicate transaction_id rows (keeps the most "complete"/most-advanced record).
  - Missing or malformed timestamp values (kept as NaT, flagged, not crashed on).
  - Out-of-order timestamps (e.g. completed_at < authorized_at) -> flagged, not used
    for elapsed-time math (would otherwise produce negative durations).
  - Non-numeric / negative / absurdly large amount_usd values -> flagged & excluded
    from amount-based aggregates rather than silently corrupting averages.
  - Unknown/blank status, corridor, or payout_method values -> bucketed as "unknown"
    so downstream filters/group-bys never throw KeyError on missing categories.
  - Empty dataframe after cleaning (e.g. every row was bad) -> exits with a clear
    message instead of producing a misleading empty report.
"""
import os
import sys

import numpy as np
import pandas as pd

RAW_PATH = "data/raw_events.csv"
OUT_PATH = "data/processed_transactions.csv"

REQUIRED_COLUMNS = [
    "transaction_id", "send_country", "receive_country", "currency_pair",
    "payout_method", "delivery_speed_label", "amount_usd", "status",
    "initiated_at", "authorized_at", "in_transit_at", "completed_at",
]

TIMESTAMP_COLS = ["initiated_at", "authorized_at", "in_transit_at", "completed_at"]

# Higher index = further along the lifecycle = "more complete" record.
STATUS_RANK = {"initiated": 0, "authorized": 1, "in_transit": 2, "failed": 3, "completed": 4}

MIN_VALID_AMOUNT = 0.01
MAX_VALID_AMOUNT = 1_000_000  # generous upper bound; anything above is almost certainly bad data


def fail(message: str, code: int = 1):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(code)


def load_raw(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        fail(
            f"Input file '{path}' not found. Run `python3 generate_data.py` first, "
            "or point RAW_PATH at your own transaction event log."
        )
    if os.path.getsize(path) == 0:
        fail(f"Input file '{path}' is empty. Nothing to process.")

    try:
        df = pd.read_csv(path, dtype={"transaction_id": str}, keep_default_na=True)
    except PermissionError:
        fail(f"Permission denied reading '{path}'. Check file permissions.")
    except pd.errors.EmptyDataError:
        fail(f"Input file '{path}' has no parseable columns/rows.")
    except pd.errors.ParserError as exc:
        fail(f"Could not parse '{path}' as CSV: {exc}")
    except Exception as exc:  # noqa: BLE001 - top-level CLI tool, surface any unexpected I/O issue
        fail(f"Unexpected error reading '{path}': {exc}")

    if df.empty:
        fail(f"Input file '{path}' contains a header but zero data rows.")

    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        fail(
            f"Input file is missing required column(s): {missing_cols}. "
            f"Expected columns: {REQUIRED_COLUMNS}"
        )

    return df


def parse_timestamps(df: pd.DataFrame) -> pd.DataFrame:
    for col in TIMESTAMP_COLS:
        # errors="coerce": malformed/missing timestamps become NaT instead of crashing the pipeline
        df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def normalize_categoricals(df: pd.DataFrame) -> pd.DataFrame:
    text_cols = ["send_country", "receive_country", "currency_pair", "payout_method",
                 "delivery_speed_label", "status"]
    for col in text_cols:
        df[col] = df[col].astype(str).str.strip()
        df.loc[df[col].isin(["", "nan", "None", "NaN"]), col] = "unknown"
        df[col] = df[col].str.lower() if col == "status" else df[col]
    return df


def coerce_amount(df: pd.DataFrame) -> pd.DataFrame:
    df["amount_usd"] = pd.to_numeric(df["amount_usd"], errors="coerce")
    df["amount_is_valid"] = df["amount_usd"].notna() & (df["amount_usd"] >= MIN_VALID_AMOUNT) & \
        (df["amount_usd"] <= MAX_VALID_AMOUNT)
    n_bad = (~df["amount_is_valid"]).sum()
    if n_bad:
        print(f"NOTE: {n_bad} row(s) have missing/invalid amount_usd; flagged via amount_is_valid=False "
              "and excluded from amount-based aggregates.")
    return df


def dedupe_transactions(df: pd.DataFrame) -> pd.DataFrame:
    if df["transaction_id"].isna().any():
        n_missing_id = df["transaction_id"].isna().sum()
        print(f"NOTE: {n_missing_id} row(s) had a missing transaction_id and were dropped "
              "(cannot reliably dedupe or join without an identifier).")
        df = df[df["transaction_id"].notna()]

    n_before = len(df)
    df["_status_rank"] = df["status"].map(STATUS_RANK).fillna(-1)
    # Among duplicate transaction_ids, keep the row that is furthest along the lifecycle;
    # ties broken by latest initiated_at so the most recently reported event wins.
    df = df.sort_values(["_status_rank", "initiated_at"]).drop_duplicates(
        subset="transaction_id", keep="last"
    )
    df = df.drop(columns="_status_rank")
    n_after = len(df)
    if n_before != n_after:
        print(f"NOTE: Removed {n_before - n_after} duplicate transaction_id row(s); "
              "kept the most advanced/most recent status per transaction.")
    return df


def flag_out_of_order(df: pd.DataFrame) -> pd.DataFrame:
    # A timestamp sequence is valid only if each non-null stage is >= the previous non-null stage.
    ordered = df[TIMESTAMP_COLS]
    is_ordered = pd.Series(True, index=df.index)
    prev_col = None
    for col in TIMESTAMP_COLS:
        if prev_col is not None:
            both_present = ordered[prev_col].notna() & ordered[col].notna()
            violates = both_present & (ordered[col] < ordered[prev_col])
            is_ordered &= ~violates
        prev_col = col
    df["timestamps_consistent"] = is_ordered
    n_bad = (~is_ordered).sum()
    if n_bad:
        print(f"NOTE: {n_bad} row(s) have out-of-order timestamps (e.g. completed before authorized); "
              "flagged via timestamps_consistent=False and excluded from time-to-completion stats.")
    return df


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    df["corridor"] = df["send_country"] + " -> " + df["receive_country"]
    df["is_completed"] = df["status"] == "completed"
    df["is_failed"] = df["status"] == "failed"
    df["is_dropped_off"] = ~df["is_completed"]  # initiated but never reached "completed"

    # Time-to-completion only computed for clean, completed, internally-consistent rows.
    valid_duration_mask = (
        df["is_completed"] & df["timestamps_consistent"] &
        df["initiated_at"].notna() & df["completed_at"].notna()
    )
    df["time_to_completion_hours"] = np.nan
    df.loc[valid_duration_mask, "time_to_completion_hours"] = (
        (df.loc[valid_duration_mask, "completed_at"] - df.loc[valid_duration_mask, "initiated_at"])
        .dt.total_seconds() / 3600.0
    )

    # Drop-off stage: last lifecycle stage reached before stopping (useful for funnel charts).
    # Vectorized (no row-wise apply) to stay well within the 50k-rows/<10s performance target.
    stage = pd.Series("unknown", index=df.index)
    is_failed = df["status"] == "failed"
    stage.loc[is_failed & df["in_transit_at"].notna()] = "failed_in_transit"
    stage.loc[is_failed & df["in_transit_at"].isna() & df["authorized_at"].notna()] = "failed_authorized"
    stage.loc[is_failed & df["in_transit_at"].isna() & df["authorized_at"].isna()] = "failed_initiated"
    stage.loc[df["completed_at"].notna() | (df["status"] == "completed")] = "completed"
    df["lifecycle_stage"] = stage

    # date bucket for time-series / date-range filtering
    df["initiated_date"] = df["initiated_at"].dt.date
    return df


def main():
    df = load_raw(RAW_PATH)
    print(f"Loaded {len(df)} raw rows from '{RAW_PATH}'.")

    df = parse_timestamps(df)
    df = normalize_categoricals(df)
    df = coerce_amount(df)
    df = dedupe_transactions(df)
    df = flag_out_of_order(df)

    if df["initiated_at"].isna().any():
        n = df["initiated_at"].isna().sum()
        print(f"NOTE: {n} row(s) have a missing initiated_at and will be excluded from "
              "date-range filtering/time-series charts (kept in the dataset otherwise).")

    df = enrich(df)

    if df.empty:
        fail("No rows remained after cleaning. Check the input data quality.")

    out_dir = os.path.dirname(OUT_PATH)
    try:
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        df.to_csv(OUT_PATH, index=False)
    except PermissionError:
        fail(f"Permission denied writing to '{OUT_PATH}'.")
    except OSError as exc:
        fail(f"Could not write output file '{OUT_PATH}': {exc}")

    print(f"Wrote {len(df)} enriched rows -> '{OUT_PATH}'.")
    print(f"  Completed: {int(df['is_completed'].sum())} | Failed: {int(df['is_failed'].sum())} | "
          f"Other/in-progress: {int((~df['is_completed'] & ~df['is_failed']).sum())}")


if __name__ == "__main__":
    main()
