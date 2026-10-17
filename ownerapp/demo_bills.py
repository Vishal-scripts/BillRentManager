"""Fake bill results so the screens can be tried with no Tata Power connection."""

from __future__ import annotations

import time
from datetime import date, timedelta

from ownerapp.db import Database, utc_now
from ownerapp.rent_logic import month_key


def demo_fetch(ca_no: str, on_progress=None, tries: int = 6, pause: float = 0) -> dict:
    if on_progress:
        on_progress({"ca_no": ca_no, "captcha_try": 0, "captcha_tries": tries, "phase": "opening"})
    steps = 2 if tries >= 2 else 1
    for attempt in range(1, steps + 1):
        if on_progress:
            on_progress(
                {
                    "ca_no": ca_no,
                    "captcha_try": attempt,
                    "captcha_tries": tries,
                    "phase": "captcha",
                }
            )
        if pause:
            time.sleep(pause)
    if on_progress:
        on_progress({"ca_no": ca_no, "captcha_try": steps, "captcha_tries": tries, "phase": "reading"})
    checked = utc_now()
    note = "Practice data. Not read from Tata Power."
    if str(ca_no).endswith("3"):
        return {
            "ca_no": ca_no,
            "status": "Error",
            "amount": None,
            "due_date": "",
            "consumer_name": "",
            "note": "Practice data kept this CA as Error. Not read from Tata Power.",
            "checked_at": checked,
            "bill_no": "",
            "bill_date": "",
            "exact_bill": None,
            "last_payment": None,
            "last_paid_on": "",
        }
    if str(ca_no).endswith("1"):
        return {
            "ca_no": ca_no,
            "status": "Unpaid",
            "amount": 100.0,
            "due_date": (date.today() + timedelta(days=2)).isoformat(),
            "consumer_name": "Practice A",
            "note": note,
            "checked_at": checked,
            "bill_no": "010000000001",
            "bill_date": date.today().replace(day=1).isoformat(),
            "exact_bill": 100.0,
            "last_payment": None,
            "last_paid_on": "",
        }
    return {
        "ca_no": ca_no,
        "status": "Paid",
        "amount": 0.0,
        "due_date": date.today().isoformat(),
        "consumer_name": "Practice B",
        "note": note,
        "checked_at": checked,
        "bill_no": "010000000002",
        "bill_date": date.today().replace(day=1).isoformat(),
        "exact_bill": 50.0,
        "last_payment": 50.0,
        "last_paid_on": (date.today() - timedelta(days=10)).isoformat(),
    }


def seed_demo(db: Database, today: date) -> None:
    if db.list_tenants():
        return
    day = today.day
    soon = min(28, day + 2) if day <= 26 else day
    late = max(1, day - 4)
    people = [
        ("Asha Mehta", "Room 1", "9811111111", 12000, day, "60000000001", "Practice row. Not a real tenant.", False),
        ("Bela Kapoor", "Room 2", "9822222222", 8000, soon, "60000000002", "Practice row. Not a real tenant.", False),
        ("Chetan Das", "Flat 3", "9833333333", 9000, late, "60000000003", "Practice row. Not a real tenant.", False),
        ("Diya Sen", "Room 4", "9844444444", 7000, min(28, max(1, day)), "60000000004", "Practice row. Not a real tenant.", True),
        ("Eshan Gill", "Shop 5", "9855555555", 15000, min(28, max(1, (day + 10) % 28 or 28)), "60000000005", "Practice row. Not a real tenant.", False),
    ]
    cycle = month_key(today)
    for name, room, phone, rent, due_day, ca, notes, paid in people:
        tenant = db.save_tenant(
            {
                "name": name,
                "room": room,
                "phone": phone,
                "rent": rent,
                "due_day": due_day,
                "ca_no": ca,
                "notes": notes,
                "active": True,
            }
        )
        if paid:
            db.mark_rent(tenant["id"], cycle, "paid", rent, today.isoformat(), "UPI", "Practice payment")
    db.save_bill(
        {
            "ca_no": "60000000001",
            "status": "Unpaid",
            "amount": 100.0,
            "due_date": (today + timedelta(days=2)).isoformat(),
            "exact_bill": 100.0,
            "bill_no": "010000000001",
            "bill_date": today.replace(day=1).isoformat(),
            "consumer_name": "Practice A",
            "last_payment": None,
            "last_paid_on": "",
            "note": "Practice data. Not read from Tata Power.",
            "checked_at": utc_now(),
            "check_state": "Done",
        }
    )
    db.save_bill(
        {
            "ca_no": "60000000002",
            "status": "Paid",
            "amount": 0.0,
            "due_date": today.isoformat(),
            "exact_bill": 50.0,
            "bill_no": "010000000002",
            "bill_date": today.replace(day=1).isoformat(),
            "consumer_name": "Practice B",
            "last_payment": 50.0,
            "last_paid_on": (today - timedelta(days=10)).isoformat(),
            "note": "Practice data. Not read from Tata Power.",
            "checked_at": utc_now(),
            "check_state": "Done",
        }
    )
    db.save_bill(
        {
            "ca_no": "60000000003",
            "status": "Error: Captcha could not be solved",
            "amount": None,
            "due_date": "",
            "exact_bill": None,
            "bill_no": "",
            "bill_date": "",
            "consumer_name": "",
            "last_payment": None,
            "last_paid_on": "",
            "note": "Practice data kept this CA as Error. Not read from Tata Power.",
            "checked_at": utc_now(),
            "check_state": "Error",
        }
    )
