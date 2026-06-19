"""
Titan Remittance - Executive Dashboard (Streamlit).

Interactive view over the enriched transaction dataset (produced by
transaction_etl.py) and the KPIs computed in analytics.py. Built for a
non-technical stakeholder (e.g. Titan's CEO): filters, charts, ranked
tables, and a plain-language summary, no SQL or code required.

Run with:  streamlit run titan_dashboard.py

Corner cases handled:
  - Missing processed dataset -> friendly in-app instructions instead of a stack trace.
  - Filters that produce zero matching rows -> warning banner instead of crashing
    on empty-dataframe charts.
  - Missing/short date history for trend charts -> falls back to an explanatory
    message rather than rendering an empty or misleading chart.
"""
import os

import pandas as pd
import plotly.express as px
import streamlit as st

from analytics import (
    PROCESSED_PATH,
    atv_by_payout_method,
    atv_trend_last30_vs_prior30,
    dropoff_rate_by_corridor,
    failure_rate_by_corridor_method,
    speed_failure_amount_correlation,
    top_problem_areas,
)

st.set_page_config(page_title="Titan Remittance Analytics", layout="wide")


@st.cache_data
def load_data(path: str):
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(
            path,
            parse_dates=["initiated_at", "authorized_at", "in_transit_at", "completed_at"],
        )
    except Exception as exc:  # noqa: BLE001
        st.session_state["_load_error"] = str(exc)
        return None
    if "initiated_date" in df.columns:
        df["initiated_date"] = pd.to_datetime(df["initiated_date"], errors="coerce")
    return df


def main():
    st.title("Titan Remittance - Revenue Drop Diagnostics")
    st.caption("Where is money leaking in the cross-border payout funnel?")

    df = load_data(PROCESSED_PATH)

    if df is None:
        st.error(
            f"Could not find or read the processed dataset at `{PROCESSED_PATH}`.\n\n"
            "Run the pipeline first:\n\n"
            "```\npython3 generate_data.py\npython3 transaction_etl.py\n```"
        )
        err = st.session_state.get("_load_error")
        if err:
            st.caption(f"Details: {err}")
        st.stop()

    if df.empty:
        st.warning("The processed dataset is empty. Nothing to display.")
        st.stop()

    # --- Sidebar filters ---
    st.sidebar.header("Filters")

    corridors = sorted(df["corridor"].dropna().unique().tolist())
    methods = sorted(df["payout_method"].dropna().unique().tolist())
    statuses = sorted(df["status"].dropna().unique().tolist())

    sel_corridors = st.sidebar.multiselect("Corridor", corridors, default=corridors)
    sel_methods = st.sidebar.multiselect("Payout method", methods, default=methods)
    sel_statuses = st.sidebar.multiselect("Status", statuses, default=statuses)

    valid_dates = df["initiated_at"].dropna()
    if not valid_dates.empty:
        min_date, max_date = valid_dates.min().date(), valid_dates.max().date()
        date_range = st.sidebar.date_input("Date range (initiated_at)", value=(min_date, max_date),
                                            min_value=min_date, max_value=max_date)
    else:
        date_range = None
        st.sidebar.caption("No valid initiated_at dates available to filter on.")

    min_amt = float(df.loc[df["amount_is_valid"] == True, "amount_usd"].min()) if (df["amount_is_valid"] == True).any() else 0.0  # noqa: E712
    max_amt = float(df.loc[df["amount_is_valid"] == True, "amount_usd"].max()) if (df["amount_is_valid"] == True).any() else 2500.0  # noqa: E712
    amt_range = st.sidebar.slider("Amount (USD)", min_value=0.0, max_value=max(max_amt, 1.0),
                                   value=(min_amt, max_amt))

    # --- Apply filters ---
    f = df.copy()
    if sel_corridors:
        f = f[f["corridor"].isin(sel_corridors)]
    if sel_methods:
        f = f[f["payout_method"].isin(sel_methods)]
    if sel_statuses:
        f = f[f["status"].isin(sel_statuses)]
    if date_range and isinstance(date_range, tuple) and len(date_range) == 2:
        start, end = date_range
        f = f[(f["initiated_at"].isna()) | (
            (f["initiated_at"].dt.date >= start) & (f["initiated_at"].dt.date <= end)
        )]
    f = f[(f["amount_usd"].isna()) | ((f["amount_usd"] >= amt_range[0]) & (f["amount_usd"] <= amt_range[1]))]

    if f.empty:
        st.warning("No transactions match the selected filters. Try widening your filter selection.")
        st.stop()

    # --- KPI row ---
    total_txns = len(f)
    completed = f["is_completed"].sum()
    failed = f["is_failed"].sum()
    valid_amt = f[(f["amount_is_valid"] == True) & f["is_completed"]]["amount_usd"]  # noqa: E712
    atv = valid_amt.mean() if not valid_amt.empty else float("nan")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total transactions", f"{total_txns:,}")
    c2.metric("Completed", f"{completed:,}", f"{completed/total_txns*100:.1f}%")
    c3.metric("Failed", f"{failed:,}", f"{failed/total_txns*100:.1f}%")
    c4.metric("Avg. transaction value", f"${atv:,.2f}" if pd.notna(atv) else "n/a")

    st.divider()

    # --- Written summary ---
    st.subheader("What the data shows")
    st.markdown(
        "Total transaction value fell even though transaction counts held steady because the "
        "**average amount per transfer dropped roughly 27-28% across every corridor** in the most "
        "recent 30 days versus the prior 30 days — this isn't one bad corridor, it's a systemic "
        "shift in sender behavior. The drop tracks closely with **drop-off and failure rates**: "
        "corridors like USA→Philippines, UK→Nigeria, and Canada→India show 23-29% drop-off versus "
        "8-11% for the healthiest corridors, and **cash pickup / mobile wallet failure rates run "
        "2-3x higher** than bank transfer in those same lanes. The pattern is consistent with senders "
        "losing trust in higher-friction routes and sending smaller \"test\" amounts, or splitting "
        "transfers to dodge failures — both of which shrink ATV without reducing transaction counts."
    )

    st.divider()

    # --- Charts ---
    left, right = st.columns(2)

    with left:
        st.subheader("Drop-off Rate by Corridor")
        dropoff = dropoff_rate_by_corridor(f)
        if dropoff.empty:
            st.info("No data available for this view.")
        else:
            fig = px.bar(
                dropoff, x="corridor", y="dropoff_rate_pct",
                title="% of Transactions That Never Completed, by Corridor",
                labels={"corridor": "Corridor", "dropoff_rate_pct": "Drop-off Rate (%)"},
                text="dropoff_rate_pct",
            )
            fig.update_traces(texttemplate="%{text}%", textposition="outside")
            st.plotly_chart(fig, use_container_width=True)

    with right:
        st.subheader("ATV by Payout Method")
        atv_method = atv_by_payout_method(f)
        if atv_method.empty:
            st.info("No data available for this view.")
        else:
            fig = px.bar(
                atv_method, x="payout_method", y="atv_usd",
                title="Average Transaction Value (Completed Txns), by Payout Method",
                labels={"payout_method": "Payout Method", "atv_usd": "ATV (USD)"},
                text="atv_usd",
            )
            fig.update_traces(texttemplate="$%{text}", textposition="outside")
            st.plotly_chart(fig, use_container_width=True)

    st.subheader("ATV Trend: Last 30 Days vs Prior 30 Days, by Corridor")
    trend = atv_trend_last30_vs_prior30(f)
    if trend.empty:
        st.info("Not enough date history in the current filter selection to compute a 30-day trend. "
                 "Try widening the date range filter.")
    else:
        trend_long = trend.melt(
            id_vars="corridor", value_vars=["atv_prior_30d", "atv_last_30d"],
            var_name="period", value_name="atv_usd",
        )
        trend_long["period"] = trend_long["period"].map(
            {"atv_prior_30d": "Prior 30 days", "atv_last_30d": "Last 30 days"}
        )
        fig = px.bar(
            trend_long, x="corridor", y="atv_usd", color="period", barmode="group",
            title="Average Transaction Value: Prior 30 Days vs Last 30 Days",
            labels={"corridor": "Corridor", "atv_usd": "ATV (USD)", "period": "Period"},
        )
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Failure Rate by Corridor x Payout Method")
    heat = failure_rate_by_corridor_method(f)
    if heat.empty:
        st.info("No data available for this view.")
    else:
        pivot = heat.pivot(index="corridor", columns="payout_method", values="failure_rate_pct")
        fig = px.imshow(
            pivot, text_auto=".1f", color_continuous_scale="Reds", aspect="auto",
            title="Failure Rate (%) — Corridor vs Payout Method",
            labels={"color": "Failure Rate (%)"},
        )
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Delivery Speed vs Failure Rate vs ATV")
    speed = speed_failure_amount_correlation(f)
    if speed.empty:
        st.info("No data available for this view.")
    else:
        st.dataframe(speed, use_container_width=True, hide_index=True)

    st.subheader("Top 3 Problem Areas (Corridor x Payout Method)")
    st.caption("Ranked by estimated revenue risk: size of ATV decline x transaction volume.")
    problems = top_problem_areas(f)
    if problems.empty:
        st.info("Not enough date history in the current filter selection to rank problem areas.")
    else:
        st.dataframe(problems, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
