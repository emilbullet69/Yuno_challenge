"""
Titan Remittance - Corridor & Payout Method Analytics.

Loads the enriched transaction dataset produced by transaction_etl.py and
computes the KPIs needed to diagnose the ATV (average transaction value)
decline: drop-off rate by corridor, ATV by payout method, failure rate by
corridor x payout method, ATV trend (last 30 days vs prior 30 days), and a
ranked list of top "problem areas" combining ATV decline with volume.

Can be run standalone (prints a text report) or imported by app.py, which
reuses every function below for the interactive dashboard.

Corner cases handled:
  - Missing processed dataset file -> clear message pointing at transaction_etl.py.
  - Empty dataframe / dataframe with too few dates to compute a 30v30 trend ->
    falls back to "insufficient data" rather than dividing by zero or crashing.
  - Groups with zero transactions (e.g. a corridor/method combo with no completed
    transactions) -> ATV reported as NaN/"n/a" instead of raising or showing 0.
  - All amount-based metrics use only rows where amount_is_valid is True, so
    bad/missing amounts injected upstream can't silently skew the averages.
"""
import os
import sys

import pandas as pd

PROCESSED_PATH = "data/processed_transactions.csv"


def fail(message: str, code: int = 1):
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(code)


def load_processed(path: str = PROCESSED_PATH) -> pd.DataFrame:
    if not os.path.exists(path):
        fail(
            f"Processed dataset '{path}' not found. Run `python3 transaction_etl.py` first "
            "to generate it from the raw event log."
        )
    if os.path.getsize(path) == 0:
        fail(f"Processed dataset '{path}' is empty.")

    try:
        df = pd.read_csv(path, parse_dates=["initiated_at", "authorized_at", "in_transit_at", "completed_at"])
    except PermissionError:
        fail(f"Permission denied reading '{path}'.")
    except Exception as exc:  # noqa: BLE001
        fail(f"Could not read processed dataset '{path}': {exc}")

    if df.empty:
        fail(f"Processed dataset '{path}' contains no rows.")

    # Defensive defaults in case an older/partial processed file is passed in.
    if "amount_is_valid" not in df.columns:
        df["amount_is_valid"] = df["amount_usd"].notna()
    if "initiated_date" in df.columns:
        df["initiated_date"] = pd.to_datetime(df["initiated_date"], errors="coerce")

    return df


def _valid_amounts(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["amount_is_valid"] == True]  # noqa: E712


def dropoff_rate_by_corridor(df: pd.DataFrame) -> pd.DataFrame:
    """% of transactions per corridor that never reached 'completed'."""
    if df.empty:
        return pd.DataFrame(columns=["corridor", "total_transactions", "dropoff_rate_pct"])
    g = df.groupby("corridor")
    out = g.agg(
        total_transactions=("transaction_id", "count"),
        dropped_off=("is_dropped_off", "sum"),
    ).reset_index()
    out["dropoff_rate_pct"] = (out["dropped_off"] / out["total_transactions"] * 100).round(1)
    return out.sort_values("dropoff_rate_pct", ascending=False)


def atv_by_payout_method(df: pd.DataFrame) -> pd.DataFrame:
    """Average transaction value for completed transactions, by payout method."""
    completed = _valid_amounts(df[df["is_completed"]])
    if completed.empty:
        return pd.DataFrame(columns=["payout_method", "completed_count", "atv_usd"])
    out = completed.groupby("payout_method").agg(
        completed_count=("transaction_id", "count"),
        atv_usd=("amount_usd", "mean"),
    ).reset_index()
    out["atv_usd"] = out["atv_usd"].round(2)
    return out.sort_values("atv_usd")


def failure_rate_by_corridor_method(df: pd.DataFrame) -> pd.DataFrame:
    """Failure rate sliced by corridor AND payout method (2-dimension slice)."""
    if df.empty:
        return pd.DataFrame(columns=["corridor", "payout_method", "total", "failure_rate_pct"])
    g = df.groupby(["corridor", "payout_method"])
    out = g.agg(
        total=("transaction_id", "count"),
        failed=("is_failed", "sum"),
    ).reset_index()
    out["failure_rate_pct"] = (out["failed"] / out["total"] * 100).round(1)
    return out.sort_values("failure_rate_pct", ascending=False)


def speed_failure_amount_correlation(df: pd.DataFrame) -> pd.DataFrame:
    """Average failure rate and ATV by delivery speed label (proxy for the
    'is faster = less reliable / smaller amounts' hypothesis)."""
    if df.empty:
        return pd.DataFrame(columns=["delivery_speed_label", "failure_rate_pct", "atv_usd", "count"])
    valid = _valid_amounts(df)
    g = df.groupby("delivery_speed_label")
    base = g.agg(count=("transaction_id", "count"), failed=("is_failed", "sum")).reset_index()
    base["failure_rate_pct"] = (base["failed"] / base["count"] * 100).round(1)

    atv = valid[valid["is_completed"]].groupby("delivery_speed_label")["amount_usd"].mean().round(2)
    base["atv_usd"] = base["delivery_speed_label"].map(atv)
    return base[["delivery_speed_label", "failure_rate_pct", "atv_usd", "count"]].sort_values(
        "failure_rate_pct", ascending=False
    )


def atv_trend_last30_vs_prior30(df: pd.DataFrame) -> pd.DataFrame:
    """Compare ATV in the most recent 30 days vs the prior 30 days, by corridor."""
    valid = _valid_amounts(df[df["is_completed"]]).copy()
    if valid.empty or valid["initiated_at"].isna().all():
        return pd.DataFrame(columns=["corridor", "atv_prior_30d", "atv_last_30d", "atv_change_pct"])

    max_date = valid["initiated_at"].max()
    if pd.isna(max_date):
        return pd.DataFrame(columns=["corridor", "atv_prior_30d", "atv_last_30d", "atv_change_pct"])

    last_30_start = max_date - pd.Timedelta(days=30)
    prior_30_start = max_date - pd.Timedelta(days=60)

    last_30 = valid[valid["initiated_at"] >= last_30_start]
    prior_30 = valid[(valid["initiated_at"] >= prior_30_start) & (valid["initiated_at"] < last_30_start)]

    if last_30.empty or prior_30.empty:
        # Not enough history to compute a meaningful 30-vs-30 comparison.
        return pd.DataFrame(columns=["corridor", "atv_prior_30d", "atv_last_30d", "atv_change_pct"])

    atv_last = last_30.groupby("corridor")["amount_usd"].mean()
    atv_prior = prior_30.groupby("corridor")["amount_usd"].mean()

    out = pd.DataFrame({"atv_prior_30d": atv_prior, "atv_last_30d": atv_last}).reset_index()
    out = out.dropna(subset=["atv_prior_30d", "atv_last_30d"])
    if out.empty:
        return out.assign(atv_change_pct=[])
    out["atv_change_pct"] = ((out["atv_last_30d"] - out["atv_prior_30d"]) / out["atv_prior_30d"] * 100).round(1)
    out[["atv_prior_30d", "atv_last_30d"]] = out[["atv_prior_30d", "atv_last_30d"]].round(2)
    return out.sort_values("atv_change_pct")


def top_problem_areas(df: pd.DataFrame, top_n: int = 3) -> pd.DataFrame:
    """Rank corridor x payout_method combos by a simple 'revenue risk' score:
    bigger ATV decline x bigger transaction volume = bigger dollar impact."""
    valid = _valid_amounts(df[df["is_completed"]]).copy()
    if valid.empty or valid["initiated_at"].isna().all():
        return pd.DataFrame(columns=["corridor", "payout_method", "volume", "atv_change_pct", "failure_rate_pct", "risk_score"])

    max_date = valid["initiated_at"].max()
    last_30_start = max_date - pd.Timedelta(days=30)
    prior_30_start = max_date - pd.Timedelta(days=60)

    last_30 = valid[valid["initiated_at"] >= last_30_start]
    prior_30 = valid[(valid["initiated_at"] >= prior_30_start) & (valid["initiated_at"] < last_30_start)]

    if last_30.empty or prior_30.empty:
        return pd.DataFrame(columns=["corridor", "payout_method", "volume", "atv_change_pct", "failure_rate_pct", "risk_score"])

    key = ["corridor", "payout_method"]
    atv_last = last_30.groupby(key)["amount_usd"].mean()
    atv_prior = prior_30.groupby(key)["amount_usd"].mean()
    vol_last = last_30.groupby(key)["amount_usd"].count()

    combined = pd.DataFrame({"atv_prior": atv_prior, "atv_last": atv_last, "volume": vol_last}).dropna(
        subset=["atv_prior", "atv_last"]
    )
    if combined.empty:
        return pd.DataFrame(columns=["corridor", "payout_method", "volume", "atv_change_pct", "failure_rate_pct", "risk_score"])

    combined["atv_change_pct"] = (combined["atv_last"] - combined["atv_prior"]) / combined["atv_prior"] * 100

    fail_rates = failure_rate_by_corridor_method(df).set_index(["corridor", "payout_method"])["failure_rate_pct"]
    combined = combined.join(fail_rates, how="left")

    # Risk score: magnitude of decline (only negative changes count) x volume, so a
    # big drop in a high-volume lane ranks above a big drop in a tiny lane.
    decline_magnitude = combined["atv_change_pct"].clip(upper=0).abs()
    combined["risk_score"] = (decline_magnitude * combined["volume"]).round(0)

    out = combined.reset_index()
    out["atv_change_pct"] = out["atv_change_pct"].round(1)
    return out.sort_values("risk_score", ascending=False).head(top_n)[
        ["corridor", "payout_method", "volume", "atv_change_pct", "failure_rate_pct", "risk_score"]
    ]


def print_report(df: pd.DataFrame):
    pd.set_option("display.width", 120)

    print("\n=== Drop-off Rate by Corridor ===")
    print(dropoff_rate_by_corridor(df).to_string(index=False))

    print("\n=== ATV by Payout Method (lowest first) ===")
    print(atv_by_payout_method(df).to_string(index=False))

    print("\n=== Failure Rate by Corridor x Payout Method (top 10) ===")
    print(failure_rate_by_corridor_method(df).head(10).to_string(index=False))

    print("\n=== Delivery Speed vs Failure Rate vs ATV ===")
    print(speed_failure_amount_correlation(df).to_string(index=False))

    print("\n=== ATV Trend: Last 30 Days vs Prior 30 Days (by corridor) ===")
    trend = atv_trend_last30_vs_prior30(df)
    print(trend.to_string(index=False) if not trend.empty else "Insufficient date range to compute 30-vs-30 trend.")

    print("\n=== Top 3 Problem Areas (corridor x payout method) ===")
    problems = top_problem_areas(df)
    print(problems.to_string(index=False) if not problems.empty else "Insufficient data to rank problem areas.")


def main():
    df = load_processed()
    print(f"Loaded {len(df)} processed rows from '{PROCESSED_PATH}'.")
    print_report(df)


if __name__ == "__main__":
    main()
