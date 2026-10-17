"""Dashboard numbers and the in-app alert list."""

from __future__ import annotations

from datetime import date, timedelta

from ownerapp.db import Database
from ownerapp.rent_logic import month_key, parse_month_key


def _parse_iso(value: str) -> date | None:
    if not value:
        return None
    try:
        year, month, day = str(value)[:10].split("-")
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def dashboard(db: Database, today: date) -> dict:
    cycle = month_key(today)
    rent_rows = db.rent_rows(cycle, today)
    due_today = [row for row in rent_rows if row["tone"] == "due" and row["due_date"] == today.isoformat()]
    due_soon = [row for row in rent_rows if row["tone"] == "due"]
    overdue = [row for row in rent_rows if row["tone"] == "overdue"]
    collected = 0.0
    pending = 0.0
    for row in rent_rows:
        rent = row["rent"] or 0
        if row["paid"]:
            collected += float(row["paid_amount"] if row["paid_amount"] not in (None, "") else rent or 0)
        elif rent:
            pending += float(rent)
    bills = db.bill_rows()
    unpaid = [row for row in bills if row["status"] == "Unpaid"]
    errors = [
        row
        for row in bills
        if row["check_state"] == "Error" or str(row["status"]).startswith("Error")
    ]
    soon_limit = today + timedelta(days=3)
    bills_soon = []
    for row in unpaid:
        due = _parse_iso(row["due_date"])
        if due and today <= due <= soon_limit:
            bills_soon.append(row)
    return {
        "today": today.isoformat(),
        "cycle": cycle,
        "cycle_label": _month_label(cycle),
        "rent_due_today": _people(due_today),
        "rent_due_soon": _people(due_soon),
        "rent_overdue": _people(overdue),
        "collected": round(collected, 2),
        "pending": round(pending, 2),
        "bills_unpaid": _bills(unpaid),
        "bills_due_soon": _bills(bills_soon),
        "bill_errors": _bills(errors),
        "unseen_alerts": db.unseen_count(),
    }


def _people(rows: list[dict]) -> dict:
    return {
        "count": len(rows),
        "names": [row["name"] or row["room"] or "Tenant" for row in rows],
    }


def _bills(rows: list[dict]) -> dict:
    return {
        "count": len(rows),
        "names": [f"{row['name'] or row['ca_no']}" for row in rows],
    }


def _month_label(cycle: str) -> str:
    year, month = parse_month_key(cycle)
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{names[month - 1]} {year}"


def sync_alerts(db: Database, today: date) -> int:
    """Store a new alert once per tenant per month. Returns how many were added."""
    added = 0
    cycle = month_key(today)
    board = dashboard(db, today)
    for row in db.rent_rows(cycle, today):
        if row["tone"] == "due":
            title = "Rent due"
            body = f"{row['name'] or 'Tenant'} — {row['tone_label'].lower()} ({row['due_date']})."
            key = f"rent-due:{row['id']}:{cycle}"
            if db.add_alert("rent", title, body, key):
                added += 1
        elif row["tone"] == "overdue":
            title = "Rent overdue"
            body = f"{row['name'] or 'Tenant'} is unpaid for {board['cycle_label']}."
            key = f"rent-overdue:{row['id']}:{cycle}"
            if db.add_alert("rent", title, body, key):
                added += 1
    for row in db.bill_rows():
        if row["status"] == "Unpaid":
            due = row["due_date"] or "no due date"
            key = f"bill-unpaid:{row['ca_no']}:{row['checked_at'] or cycle}"
            if db.add_alert("bill", "Electricity unpaid", f"{row['name']} CA {row['ca_no']} due {due}.", key):
                added += 1
        elif row["check_state"] == "Error" or str(row["status"]).startswith("Error"):
            key = f"bill-error:{row['ca_no']}:{row['checked_at'] or row['note'][:40]}"
            if db.add_alert("bill", "Bill check error", f"{row['name']} CA {row['ca_no']}: {row['note'] or row['status']}", key):
                added += 1
    return added
