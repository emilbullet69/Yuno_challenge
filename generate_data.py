"""
Generates synthetic remittance transaction event data for Titan Remittance.

Simulates ~52,000 transactions across 6 corridors x 3 payout methods over 75 days,
with a deliberate ATV decline in the most recent 30 days and elevated failure
rates in 2-3 "problem" corridors. Also injects realistic data quality issues:
duplicate transaction IDs, missing timestamps, and out-of-order timestamps.
"""
import csv
import os
import random
import sys
import uuid
from datetime import datetime, timedelta

random.seed(42)

CORRIDORS = [
    # (send_country, receive_country, currency_pair, base_failure_rate, atv_base, is_problem)
    ("USA", "Mexico", "USD->MXN", 0.08, 280, False),
    ("USA", "Philippines", "USD->PHP", 0.27, 240, True),   # problem corridor
    ("Spain", "Colombia", "EUR->COP", 0.10, 220, False),
    ("UK", "Nigeria", "GBP->NGN", 0.25, 200, True),        # problem corridor
    ("Germany", "Vietnam", "EUR->VND", 0.09, 260, False),
    ("Canada", "India", "CAD->INR", 0.22, 230, True),      # problem corridor
]

PAYOUT_METHODS = [
    # (method, speed_label, hours_to_complete_range, fail_rate_multiplier, amount_multiplier)
    ("bank_transfer", "1-3 days", (18, 72), 0.9, 1.25),
    ("mobile_wallet", "instant", (0.05, 1), 1.0, 0.85),
    ("cash_pickup", "instant", (0.1, 6), 1.3, 1.0),
]

STATUSES_ORDER = ["initiated", "authorized", "in_transit", "completed"]

NUM_TRANSACTIONS = 52000
DAYS_SPAN = 75
END_DATE = datetime(2025, 6, 1)
START_DATE = END_DATE - timedelta(days=DAYS_SPAN)
RECENT_CUTOFF = END_DATE - timedelta(days=30)


def random_timestamp_in_range():
    delta_seconds = int((END_DATE - START_DATE).total_seconds())
    offset = random.randint(0, delta_seconds)
    return START_DATE + timedelta(seconds=offset)


def gen_amount(corridor_atv_base, method_multiplier, is_recent):
    # Base amount clustered around corridor/method ATV, with long tail up to 2500
    base = max(10, random.gauss(corridor_atv_base * method_multiplier, 60))
    if is_recent:
        # Recent 30 days: senders break large transfers into smaller ones / send less
        base *= random.uniform(0.6, 0.85)
    amount = min(2500, max(10, base))
    return round(amount, 2)


def gen_transaction(events, txn_id, send_country, receive_country, currency_pair,
                     failure_rate, atv_base, method, speed_label, hours_range,
                     fail_multiplier, amount_multiplier, inject_quality_issues=True):
    initiated_at = random_timestamp_in_range()
    is_recent = initiated_at >= RECENT_CUTOFF
    amount = gen_amount(atv_base, amount_multiplier, is_recent)

    effective_fail_rate = min(0.95, failure_rate * fail_multiplier)
    will_fail = random.random() < effective_fail_rate

    # Determine how far the transaction progresses before stopping/failing
    if will_fail:
        fail_at_stage = random.choices(
            ["initiated", "authorized", "in_transit"],
            weights=[0.2, 0.35, 0.45],
        )[0]
    else:
        fail_at_stage = None

    timestamps = {"initiated_at": initiated_at}
    cursor = initiated_at
    hours_min, hours_max = hours_range

    stage_order = ["authorized", "in_transit", "completed"]
    for stage in stage_order:
        if fail_at_stage:
            idx_fail = ["initiated", "authorized", "in_transit"].index(fail_at_stage)
            idx_stage = stage_order.index(stage)
            if idx_stage > idx_fail:
                break
        step_hours = random.uniform(hours_min, hours_max) / len(stage_order)
        cursor = cursor + timedelta(hours=step_hours)
        if stage == "completed":
            timestamps["completed_at"] = cursor
        elif stage == "authorized":
            timestamps["authorized_at"] = cursor
        elif stage == "in_transit":
            timestamps["in_transit_at"] = cursor

    final_status = "failed" if will_fail else "completed"

    row = {
        "transaction_id": txn_id,
        "send_country": send_country,
        "receive_country": receive_country,
        "currency_pair": currency_pair,
        "payout_method": method,
        "delivery_speed_label": speed_label,
        "amount_usd": amount,
        "status": final_status,
        "initiated_at": timestamps.get("initiated_at", ""),
        "authorized_at": timestamps.get("authorized_at", ""),
        "in_transit_at": timestamps.get("in_transit_at", ""),
        "completed_at": timestamps.get("completed_at", ""),
    }

    # --- Inject data quality issues ---
    if inject_quality_issues:
        r = random.random()
        if r < 0.01:
            # missing timestamp field
            field = random.choice(["authorized_at", "in_transit_at"])
            row[field] = ""
        elif r < 0.015 and row["completed_at"]:
            # out-of-order timestamp: completed_at recorded before authorized_at
            row["completed_at"], row["authorized_at"] = row["authorized_at"], row["completed_at"]

    events.append(row)

    # Duplicate transaction injection (~1.5% of rows get a duplicate event re-emitted)
    if inject_quality_issues and random.random() < 0.015:
        dup_row = dict(row)
        # Simulate a re-sent/late-arriving duplicate event, possibly with status update
        events.append(dup_row)


def main():
    num_combos = len(CORRIDORS) * len(PAYOUT_METHODS)
    if num_combos == 0:
        print("ERROR: No corridors or payout methods configured. Nothing to generate.", file=sys.stderr)
        sys.exit(1)

    per_combo = NUM_TRANSACTIONS // num_combos
    if per_combo <= 0:
        print(
            f"ERROR: NUM_TRANSACTIONS ({NUM_TRANSACTIONS}) is too low to cover "
            f"{num_combos} corridor/payout-method combinations. Increase NUM_TRANSACTIONS.",
            file=sys.stderr,
        )
        sys.exit(1)

    events = []
    txn_counter = 0

    try:
        for send_country, receive_country, currency_pair, failure_rate, atv_base, is_problem in CORRIDORS:
            for method, speed_label, hours_range, fail_mult, amount_mult in PAYOUT_METHODS:
                for _ in range(per_combo):
                    txn_counter += 1
                    txn_id = f"TXN-{txn_counter:07d}-{uuid.uuid4().hex[:6]}"
                    gen_transaction(
                        events, txn_id, send_country, receive_country, currency_pair,
                        failure_rate, atv_base, method, speed_label, hours_range,
                        fail_mult, amount_mult,
                    )
    except KeyboardInterrupt:
        print("\nGeneration cancelled by user. No file was written.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"ERROR: Failed while generating transaction {txn_counter}: {exc}", file=sys.stderr)
        sys.exit(1)

    if not events:
        print("ERROR: No events were generated. Aborting before writing an empty file.", file=sys.stderr)
        sys.exit(1)

    random.shuffle(events)

    fieldnames = [
        "transaction_id", "send_country", "receive_country", "currency_pair",
        "payout_method", "delivery_speed_label", "amount_usd", "status",
        "initiated_at", "authorized_at", "in_transit_at", "completed_at",
    ]

    out_path = "data/raw_events.csv"
    out_dir = os.path.dirname(out_path)
    try:
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        print(f"ERROR: Could not create output directory '{out_dir}': {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(out_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in events:
                writer.writerow(row)
    except PermissionError:
        print(f"ERROR: Permission denied writing to '{out_path}'. Check folder permissions.", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"ERROR: Could not write output file '{out_path}': {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Generated {len(events)} events -> {out_path}")


if __name__ == "__main__":
    main()
