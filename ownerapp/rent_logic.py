"""Rent due dates, colours, and the monthly rollover. No database and no network."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date


def parse_due_day(label) -> int | None:
    if label is None:
        return None
    if isinstance(label, int) and not isinstance(label, bool):
        day = label
    elif isinstance(label, float):
        if label != int(label):
            return None
        day = int(label)
    else:
        text = str(label).strip()
        if not text:
            return None
        match = re.match(r"^(\d{1,2})\s*(st|nd|rd|th)?$", text, re.IGNORECASE)
        if not match:
            return None
        day = int(match.group(1))
    if day < 1 or day > 31:
        return None
    return day


def parse_rent(value) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = re.sub(r"[,₹\s]", "", str(value).strip())
        text = re.sub(r"^rs\.?", "", text, flags=re.IGNORECASE)
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    if number <= 0:
        return None
    return number


def clean_phone(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        value = int(value)
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 12 and digits.startswith("91"):
        return digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        return digits[1:]
    return digits


def normalize_ca(raw) -> str | None:
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    return digits if len(digits) == 11 else None


def month_key(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def parse_month_key(key: str) -> tuple[int, int]:
    year, month = str(key).split("-")
    return int(year), int(month)


def previous_month_key(day: date) -> str:
    if day.month == 1:
        return f"{day.year - 1:04d}-12"
    return f"{day.year:04d}-{day.month - 1:02d}"


def next_month_key(key: str) -> str:
    year, month = parse_month_key(key)
    if month == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{month + 1:02d}"


def shift_month_key(key: str, delta: int) -> str:
    current = key
    if delta >= 0:
        for _ in range(delta):
            current = next_month_key(current)
        return current
    for _ in range(-delta):
        year, month = parse_month_key(current)
        current = previous_month_key(date(year, month, 1))
    return current


def due_date_in_month(year: int, month: int, due_day: int | None) -> date | None:
    if not due_day:
        return None
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(due_day, last))


@dataclass(frozen=True)
class RentTone:
    tone: str
    due: date | None
    label: str


def rent_tone(today: date, due_day: int | None, paid: bool, cycle: str | None = None) -> RentTone:
    """Colour for one tenant in one month.

    Green: paid. Orange: due today or within the next 2 days. Red: overdue.
    A past month is green or red only. A future month is green or plain upcoming.
    """
    cycle = cycle or month_key(today)
    year, month = parse_month_key(cycle)
    due = due_date_in_month(year, month, due_day)
    current = month_key(today)
    if paid:
        return RentTone("paid", due, "Paid")
    if cycle < current:
        return RentTone("overdue", due, "Unpaid")
    if cycle > current:
        return RentTone("upcoming", due, "Upcoming")
    if due is None:
        return RentTone("none", None, "No due day")
    if today > due:
        return RentTone("overdue", due, "Overdue")
    if (due - today).days <= 2:
        word = "Due today" if today == due else "Due soon"
        return RentTone("due", due, word)
    return RentTone("upcoming", due, "Upcoming")


def months_to_close(today: date, closed_through: str | None) -> list[str]:
    """Months that should be frozen into history.

    The first launch closes nothing. It only remembers the previous month, so a
    new install does not invent a year of unpaid rows. After that, each new
    calendar month closes the months that have finished.
    """
    previous = previous_month_key(today)
    if not closed_through:
        return []
    if closed_through >= previous:
        return []
    months = []
    cursor = next_month_key(closed_through)
    while cursor <= previous:
        months.append(cursor)
        cursor = next_month_key(cursor)
    return months


def initial_closed_through(today: date) -> str:
    return previous_month_key(today)
