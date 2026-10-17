"""Read tenants from the old sheet CSV or from an Excel file."""

from __future__ import annotations

import csv
import io
from pathlib import Path

from openpyxl import load_workbook

from ownerapp.contacts import mobile_ok, normalize_mobile
from ownerapp.rent_logic import normalize_ca, parse_due_day, parse_rent

_ALIASES = {
    "name": {"name", "tenant", "tenant name"},
    "room": {"room", "flat", "address", "room/flat", "room / flat"},
    "phone": {"phone", "ph. no", "ph no", "ph.no", "mobile", "mobile no"},
    "rent": {"rent", "monthly rent"},
    "due_day": {"due day", "due", "date"},
    "ca_no": {"ca", "ca.no", "ca no", "ca number", "cano"},
    "notes": {"notes", "note"},
    "status": {"status"},
}


def _header_map(headers: list[str]) -> dict[str, int]:
    found = {}
    for index, header in enumerate(headers):
        key = " ".join(str(header or "").strip().lower().replace("_", " ").split())
        for field, names in _ALIASES.items():
            if key in names and field not in found:
                found[field] = index
    return found


def _cell(row: list, index: int | None):
    if index is None or index >= len(row):
        return ""
    value = row[index]
    return "" if value is None else value


def rows_from_table(headers: list[str], table: list[list]) -> list[dict]:
    mapping = _header_map(headers)
    if "name" not in mapping and "ca_no" not in mapping:
        raise ValueError("The file needs a NAME column or a CA.NO column.")
    parsed = []
    for raw in table:
        if not any(str(cell or "").strip() for cell in raw):
            continue
        name = str(_cell(raw, mapping.get("name")) or "").strip()
        room = str(_cell(raw, mapping.get("room")) or "").strip()
        phone_cell = _cell(raw, mapping.get("phone"))
        rent_cell = _cell(raw, mapping.get("rent"))
        due_cell = _cell(raw, mapping.get("due_day"))
        ca_raw = _cell(raw, mapping.get("ca_no"))
        notes = str(_cell(raw, mapping.get("notes")) or "").strip()
        status = str(_cell(raw, mapping.get("status")) or "").strip().lower()
        ca = normalize_ca(ca_raw) or ""
        phone = normalize_mobile(phone_cell) if str(phone_cell).strip() else ""
        rent = parse_rent(rent_cell)
        due = parse_due_day(due_cell)
        issues = []
        if str(ca_raw).strip() and not ca:
            issues.append("CA must be 11 digits.")
        if str(phone_cell).strip() and not mobile_ok(phone_cell):
            issues.append("Phone must be 10 digits.")
        if str(rent_cell).strip() and rent is None:
            issues.append("Rent must be a number.")
        if str(due_cell).strip() and due is None:
            issues.append("Due day must be from 1 to 31.")
        if not name and not ca and not issues:
            continue
        parsed.append(
            {
                "name": name,
                "room": room,
                "phone": phone,
                "rent": rent,
                "due_day": due,
                "ca_no": ca,
                "notes": notes,
                "mark_paid": status in {"paid", "yes", "y", "true"},
                "issues": issues,
                "row_number": len(parsed) + 2,
            }
        )
    return parsed


def read_csv(payload: bytes | str) -> list[dict]:
    if isinstance(payload, bytes):
        text = payload.decode("utf-8-sig")
    else:
        text = payload
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []
    return rows_from_table(rows[0], rows[1:])


def read_xlsx(payload: bytes) -> list[dict]:
    workbook = load_workbook(io.BytesIO(payload), data_only=True, read_only=True)
    sheet = workbook["Tenants"] if "Tenants" in workbook.sheetnames else workbook.active
    table = [list(row) for row in sheet.iter_rows(values_only=True)]
    workbook.close()
    if not table:
        return []
    headers = ["" if cell is None else str(cell) for cell in table[0]]
    return rows_from_table(headers, table[1:])


def read_upload(filename: str, payload: bytes) -> list[dict]:
    lower = filename.lower()
    if lower.endswith(".xlsx"):
        return read_xlsx(payload)
    if lower.endswith(".csv") or lower.endswith(".txt"):
        return read_csv(payload)
    raise ValueError("Upload a .csv or .xlsx file.")


def read_path(path: Path) -> list[dict]:
    return read_upload(path.name, path.read_bytes())
