"""
Generates static PNG exports of the dashboard's charts/tables for the
deliverables folder, so a reviewer can see sample output without running
Streamlit. Reuses the exact same analytics functions as titan_dashboard.py.

Run with: python3 generate_screenshots.py
"""
import os

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from analytics import (
    PROCESSED_PATH,
    atv_by_payout_method,
    atv_trend_last30_vs_prior30,
    dropoff_rate_by_corridor,
    failure_rate_by_corridor_method,
    speed_failure_amount_correlation,
    top_problem_areas,
)

OUT_DIR = "screenshots"


def df_to_table_fig(df: pd.DataFrame, title: str) -> go.Figure:
    fig = go.Figure(data=[go.Table(
        header=dict(values=list(df.columns), fill_color="#2c3e50", font=dict(color="white", size=12),
                    align="left"),
        cells=dict(values=[df[c] for c in df.columns], align="left"),
    )])
    fig.update_layout(title=title, margin=dict(l=10, r=10, t=40, b=10))
    return fig


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    df = pd.read_csv(
        PROCESSED_PATH,
        parse_dates=["initiated_at", "authorized_at", "in_transit_at", "completed_at"],
    )

    dropoff = dropoff_rate_by_corridor(df)
    fig = px.bar(
        dropoff, x="corridor", y="dropoff_rate_pct",
        title="% of Transactions That Never Completed, by Corridor",
        labels={"corridor": "Corridor", "dropoff_rate_pct": "Drop-off Rate (%)"},
        text="dropoff_rate_pct",
    )
    fig.update_traces(texttemplate="%{text}%", textposition="outside")
    fig.write_image(f"{OUT_DIR}/01_dropoff_rate_by_corridor.png", width=900, height=550, scale=2)

    atv_method = atv_by_payout_method(df)
    fig = px.bar(
        atv_method, x="payout_method", y="atv_usd",
        title="Average Transaction Value (Completed Txns), by Payout Method",
        labels={"payout_method": "Payout Method", "atv_usd": "ATV (USD)"},
        text="atv_usd",
    )
    fig.update_traces(texttemplate="$%{text}", textposition="outside")
    fig.write_image(f"{OUT_DIR}/02_atv_by_payout_method.png", width=900, height=550, scale=2)

    trend = atv_trend_last30_vs_prior30(df)
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
    fig.write_image(f"{OUT_DIR}/03_atv_trend_30v30.png", width=1000, height=550, scale=2)

    heat = failure_rate_by_corridor_method(df)
    pivot = heat.pivot(index="corridor", columns="payout_method", values="failure_rate_pct")
    fig = px.imshow(
        pivot, text_auto=".1f", color_continuous_scale="Reds", aspect="auto",
        title="Failure Rate (%) — Corridor vs Payout Method",
        labels={"color": "Failure Rate (%)"},
    )
    fig.write_image(f"{OUT_DIR}/04_failure_rate_heatmap.png", width=900, height=550, scale=2)

    speed = speed_failure_amount_correlation(df)
    fig = df_to_table_fig(speed, "Delivery Speed vs Failure Rate vs ATV")
    fig.write_image(f"{OUT_DIR}/05_speed_failure_atv_table.png", width=800, height=300, scale=2)

    problems = top_problem_areas(df)
    fig = df_to_table_fig(problems, "Top 3 Problem Areas (Corridor x Payout Method)")
    fig.write_image(f"{OUT_DIR}/06_top_problem_areas.png", width=1000, height=260, scale=2)

    print(f"Wrote 6 chart/table PNGs to '{OUT_DIR}/'.")


if __name__ == "__main__":
    main()
