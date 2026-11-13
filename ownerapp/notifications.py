"""Unified notification feed for rent and electricity bills."""

from __future__ import annotations

from datetime import date, timedelta

from ownerapp.alerts import clean_alert_text
from ownerapp.db import Database
from ownerapp.rent_logic import month_key


def _parse_iso(value: str) -> date | None:
    if not value:
        return None
    try:
        y, m, d = str(value)[:10].split("-")
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def _days_until(today: date, due: date | None) -> int | None:
    if due is None:
        return None
    return (due - today).days


def _urgency(category: str, days: int | None) -> int:
    order = {
        "rent_overdue": 0,
        "bill_overdue": 1,
        "rent_due_today": 2,
        "bill_due_soon": 3,
        "rent_due_soon": 4,
        "bill_unpaid": 5,
        "bill_error": 6,
    }
    base = order.get(category, 50)
    if days is not None and days < 0:
        return max(0, base - 1)
    return base


def bill_chip_status(row: dict, today: date) -> str:
    status = str(row.get("status") or "")
    check = str(row.get("check_state") or "")
    if check == "Error" or status.startswith("Error"):
        return "Error"
    if status == "Paid":
        return "Paid"
    if status != "Unpaid":
        return ""
    due = _parse_iso(row.get("due_date") or "")
    if due and due < today:
        return "Overdue"
    if due and (due - today).days <= 3:
        return "Due soon"
    return "Unpaid"


def build_notifications(db: Database, today: date) -> list[dict]:
    cycle = month_key(today)
    rent_rows = db.rent_rows(cycle, today)
    bills = {row["ca_no"]: row for row in db.bill_rows()}
    tenants = db.list_tenants()
    reads = db.notification_reads()
    items: list[dict] = []

    for row in rent_rows:
        if not row.get("active"):
            continue
        tenant_id = row["id"]
        name = row.get("name") or row.get("room") or "Tenant"
        room = row.get("room") or ""
        rent = row.get("rent")
        due_date = row.get("due_date") or ""
        days = _days_until(today, _parse_iso(due_date))
        base = {
            "tenant_id": tenant_id,
            "name": name,
            "room": room,
            "ca_no": row.get("ca_no") or "",
            "amount": rent,
            "due_date": due_date,
            "days": days,
            "read": False,
        }
        if row["tone"] == "overdue":
            key = f"rent-overdue:{tenant_id}:{cycle}"
            items.append(
                {
                    **base,
                    "id": key,
                    "kind": "rent",
                    "category": "rent_overdue",
                    "title": "Rent overdue",
                    "body": clean_alert_text(f"{name} - unpaid for this month."),
                    "urgency": _urgency("rent_overdue", days),
                }
            )
        elif row["tone"] == "due" and due_date == today.isoformat():
            key = f"rent-due-today:{tenant_id}:{cycle}"
            items.append(
                {
                    **base,
                    "id": key,
                    "kind": "rent",
                    "category": "rent_due_today",
                    "title": "Rent due today",
                    "body": clean_alert_text(f"{name} - due today."),
                    "urgency": _urgency("rent_due_today", 0),
                }
            )
        elif row["tone"] == "due" and days is not None and 0 <= days <= 3:
            key = f"rent-due-soon:{tenant_id}:{cycle}"
            items.append(
                {
                    **base,
                    "id": key,
                    "kind": "rent",
                    "category": "rent_due_soon",
                    "title": "Rent due in 3 days",
                    "body": clean_alert_text(f"{name} - due in {days} day(s)."),
                    "urgency": _urgency("rent_due_soon", days),
                }
            )

    soon_limit = today + timedelta(days=3)
    for tenant in tenants:
        if not tenant.get("active"):
            continue
        bill = bills.get(tenant.get("ca_no") or "")
        if not bill:
            continue
        chip = bill_chip_status(bill, today)
        name = bill.get("name") or tenant.get("name") or tenant.get("ca_no")
        room = bill.get("room") or tenant.get("room") or ""
        due = _parse_iso(bill.get("due_date") or "")
        days = _days_until(today, due)
        base = {
            "tenant_id": tenant["id"],
            "name": name,
            "room": room,
            "ca_no": bill["ca_no"],
            "amount": bill.get("amount"),
            "due_date": bill.get("due_date") or "",
            "days": days,
            "read": False,
        }
        if chip == "Error":
            key = f"bill-error:{bill['ca_no']}:{bill.get('checked_at') or bill.get('note', '')[:30]}"
            items.append(
                {
                    **base,
                    "id": key,
                    "kind": "electricity",
                    "category": "bill_error",
                    "title": "Bill check error",
                    "body": clean_alert_text(bill.get("note") or bill.get("status") or "Error"),
                    "urgency": _urgency("bill_error", days),
                }
            )
        elif chip == "Overdue":
            key = f"bill-overdue:{bill['ca_no']}:{bill.get('due_date')}"
            items.append(
                {
                    **base,
                    "id": key,
                    "kind": "electricity",
                    "category": "bill_overdue",
                    "title": "Electricity bill overdue",
                    "body": clean_alert_text(f"{name} - outstanding {bill.get('amount')}."),
                    "urgency": _urgency("bill_overdue", days),
                }
            )
        elif chip == "Due soon":
            key = f"bill-due-soon:{bill['ca_no']}:{bill.get('due_date')}"
            items.append(
                {
                    **base,
                    "id": key,
                    "kind": "electricity",
                    "category": "bill_due_soon",
                    "title": "Bill due in 3 days",
                    "body": clean_alert_text(f"{name} - due in {days} day(s)."),
                    "urgency": _urgency("bill_due_soon", days),
                }
            )
        elif chip == "Unpaid":
            key = f"bill-unpaid:{bill['ca_no']}:{bill.get('checked_at')}"
            items.append(
                {
                    **base,
                    "id": key,
                    "kind": "electricity",
                    "category": "bill_unpaid",
                    "title": "Electricity bill unpaid",
                    "body": clean_alert_text(f"{name} - outstanding {bill.get('amount')}."),
                    "urgency": _urgency("bill_unpaid", days),
                }
            )

    for item in items:
        item["read"] = item["id"] in reads
    items.sort(key=lambda row: (row["urgency"], row.get("due_date") or "9999", row["name"]))
    return items


def unread_count(items: list[dict]) -> int:
    return sum(1 for item in items if not item.get("read"))
