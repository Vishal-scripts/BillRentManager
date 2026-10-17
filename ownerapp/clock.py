"""Show timestamps in India Standard Time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def format_ist(value, with_time: bool = True) -> str:
    """Format an ISO timestamp or a YYYY-MM-DD date for a person to read."""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        year, month, day = (int(part) for part in text.split("-"))
        return f"{day} {_MONTHS[month - 1]} {year}"
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(IST)
    if not with_time:
        return f"{local.day} {_MONTHS[local.month - 1]} {local.year}"
    hour = local.hour % 12 or 12
    suffix = "AM" if local.hour < 12 else "PM"
    return f"{local.day} {_MONTHS[local.month - 1]} {local.year}, {hour}:{local.minute:02d} {suffix}"
