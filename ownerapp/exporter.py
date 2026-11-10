"""Excel export with summary, tenants, rent, bills, and notifications."""

from __future__ import annotations

import io
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ownerapp.clock import format_ist
from ownerapp.db import Database
from ownerapp.notifications import bill_chip_status, build_notifications
from ownerapp.rent_logic import month_key

HEADER_FILL = PatternFill("solid", fgColor="145C54")
HEADER_FONT = Font(color="FFFFFF", bold=True, name="Calibri")
FILLS = {
    "paid": PatternFill("solid", fgColor="D9EAD3"),
    "due": PatternFill("solid", fgColor="FCE5CD"),
    "overdue": PatternFill("solid", fgColor="F4CCCC"),
    "upcoming": PatternFill("solid", fgColor="FFFFFF"),
    "none": PatternFill("solid", fgColor="FFF8E7"),
    "error": PatternFill("solid", fgColor="F4CCCC"),
}
RS_FORMAT = '"Rs" #,##0.00'
DATE_FORMAT = "dd-mmm-yyyy"


def _tone_for_rent(tone: str) -> str:
    return tone if tone in FILLS else "none"


def _tone_for_bill(status: str, chip: str) -> str:
    if chip == "Error":
        return "error"
    if chip in ("Overdue",):
        return "overdue"
    if chip in ("Unpaid", "Due soon"):
        return "due"
    if status == "Paid":
        return "paid"
    return "none"


def _write_sheet(
    sheet,
    headers: list[str],
    rows: list[list],
    tones: list[str],
    *,
    money_cols: set[int] | None = None,
    date_cols: set[int] | None = None,
) -> None:
    sheet.append(headers)
    for cell in sheet[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    money_cols = money_cols or set()
    date_cols = date_cols or set()
    for values, tone in zip(rows, tones):
        sheet.append(values)
        fill = FILLS.get(tone) or FILLS["none"]
        for index, cell in enumerate(sheet[sheet.max_row], start=1):
            if isinstance(cell.value, str) and cell.value[:1] in ("=", "+", "-", "@"):
                # Keep the characters as text. Excel must not run them as a formula.
                cell.data_type = "s"
            cell.fill = fill
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            if index in money_cols and isinstance(cell.value, (int, float)):
                cell.number_format = RS_FORMAT
            if index in date_cols and cell.value:
                cell.number_format = DATE_FORMAT
    for index, header in enumerate(headers, start=1):
        longest = len(str(header))
        for values in rows:
            if index - 1 < len(values):
                longest = max(longest, len(str(values[index - 1] or "")))
        sheet.column_dimensions[get_column_letter(index)].width = max(12, min(44, longest + 2))
    if sheet.max_row > 1:
        sheet.auto_filter.ref = sheet.dimensions
    sheet.freeze_panes = "A2"
    sheet.row_dimensions[1].height = 22


def build_workbook(db: Database, today: date) -> bytes:
    cycle = month_key(today)
    rent_rows = db.rent_rows(cycle, today)
    tone_by_id = {row["id"]: row["tone"] for row in rent_rows}
    bills = {row["ca_no"]: row for row in db.bill_rows()}
    book = Workbook()

    # Summary (first sheet)
    summary = book.active
    summary.title = "Summary"
    sum_headers = [
        "Name",
        "Room / flat",
        "Phone",
        "CA number",
        "Monthly rent",
        "Rent due day",
        "Rent status this month",
        "Rent paid on",
        "Rent paid amount",
        "Consumer on bill",
        "Bill no",
        "Bill date",
        "Bill due date",
        "Exact bill (Rs)",
        "Outstanding (Rs)",
        "Bill status",
        "Last checked (IST)",
    ]
    sum_values: list[list] = []
    sum_tones: list[str] = []
    total_rent = collected = pending = elec_out = 0.0
    for tenant in db.list_tenants():
        rent_row = next((row for row in rent_rows if row["id"] == tenant["id"]), None)
        bill = bills.get(tenant.get("ca_no") or "", {})
        rent = tenant.get("rent") or 0
        if tenant.get("active") and rent:
            total_rent += float(rent)
        tone = tone_by_id.get(tenant["id"], "none")
        if rent_row and rent_row.get("paid"):
            paid_amt = rent_row.get("paid_amount") or rent_row.get("rent") or 0
            collected += float(paid_amt or 0)
        elif tenant.get("active") and rent:
            pending += float(rent)
        chip = bill_chip_status(bill, today) if bill else ""
        if bill and chip and chip not in ("Paid", "Error") and bill.get("amount"):
            elec_out += float(bill["amount"] or 0)
        sum_values.append(
            [
                tenant["name"],
                tenant["room"],
                tenant["phone"],
                tenant["ca_no"],
                tenant["rent"] if tenant["rent"] is not None else "",
                tenant["due_day"] or "",
                rent_row["tone_label"] if rent_row else "",
                format_ist(rent_row["paid_on"], with_time=False) if rent_row and rent_row.get("paid_on") else "",
                rent_row["paid_amount"] if rent_row and rent_row.get("paid_amount") is not None else "",
                bill.get("consumer_name") if bill else "",
                bill.get("bill_no") if bill else "",
                bill.get("bill_date") if bill else "",
                bill.get("due_date") if bill else "",
                bill.get("exact_bill") if bill else "",
                bill.get("amount") if bill else "",
                chip or bill.get("status") if bill else "",
                format_ist(bill.get("checked_at")) if bill else "",
            ]
        )
        sum_tones.append(_tone_for_bill(bill.get("status") if bill else "", chip) if bill else _tone_for_rent(tone))
    _write_sheet(
        summary,
        sum_headers,
        sum_values,
        sum_tones,
        money_cols={5, 9, 14, 15},
        date_cols={8, 12, 13},
    )
    total_row = summary.max_row + 2
    summary.cell(total_row, 1, "Totals")
    summary.cell(total_row, 5, total_rent)
    summary.cell(total_row, 5).number_format = RS_FORMAT
    summary.cell(total_row, 9, collected)
    summary.cell(total_row, 9).number_format = RS_FORMAT
    summary.cell(total_row, 6, "Pending rent")
    summary.cell(total_row, 7, pending)
    summary.cell(total_row, 7).number_format = RS_FORMAT
    summary.cell(total_row, 15, elec_out)
    summary.cell(total_row, 15).number_format = RS_FORMAT

    tenants = book.create_sheet("Tenants")
    tenant_headers = [
        "Name",
        "Room / flat",
        "Phone",
        "Monthly rent",
        "Due day",
        "CA number",
        "Notes",
        "Active",
        "This month",
    ]
    tenant_values = []
    tenant_tones = []
    for row in db.list_tenants():
        tone = tone_by_id.get(row["id"], "none" if row["active"] else "none")
        label = next((item["tone_label"] for item in rent_rows if item["id"] == row["id"]), "")
        tenant_values.append(
            [
                row["name"],
                row["room"],
                row["phone"],
                row["rent"] if row["rent"] is not None else "",
                row["due_day"] or "",
                row["ca_no"],
                row["notes"],
                "Yes" if row["active"] else "No",
                label,
            ]
        )
        tenant_tones.append(tone if row["active"] else "none")
    _write_sheet(tenants, tenant_headers, tenant_values, tenant_tones, money_cols={4})

    rent = book.create_sheet("Rent payments")
    rent_headers = ["Month", "Name", "Room", "Rent", "Status", "Amount", "Paid on", "Mode", "Note"]
    history = db.rent_entries_for_export()
    rent_values = []
    rent_tones = []
    for row in history:
        tone = "paid" if row["status"] == "paid" else "overdue"
        rent_values.append(
            [
                row["cycle"],
                row["name"],
                row["room"],
                "",
                "Paid" if row["status"] == "paid" else "Unpaid",
                row["amount"] if row["amount"] is not None else "",
                format_ist(row["paid_on"], with_time=False) if row["paid_on"] else "",
                row["mode"],
                row["note"],
            ]
        )
        rent_tones.append(tone)
    _write_sheet(rent, rent_headers, rent_values, rent_tones, money_cols={6}, date_cols={7})

    elec = book.create_sheet("Electricity bills")
    elec_headers = [
        "Record",
        "Name",
        "Room",
        "CA number",
        "Status",
        "Outstanding",
        "Due date",
        "Exact bill",
        "Bill no",
        "Bill date",
        "Consumer",
        "Checked at (IST)",
        "Note",
    ]
    elec_values = []
    elec_tones = []
    history_rows = db.bill_history_rows()
    for row in db.bill_rows():
        chip = bill_chip_status(row, today)
        tone = _tone_for_bill(row["status"], chip)
        elec_values.append(
            [
                "Latest",
                row["name"],
                row["room"],
                row["ca_no"],
                chip or row["status"],
                row["amount"] if row["amount"] is not None else "",
                row["due_date"] or "",
                row["exact_bill"] if row["exact_bill"] is not None else "",
                row["bill_no"],
                row["bill_date"] or "",
                row["consumer_name"],
                format_ist(row["checked_at"]),
                row["note"],
            ]
        )
        elec_tones.append(tone)
    for row in history_rows:
        chip = bill_chip_status(row, today) if row.get("status") else ""
        tone = _tone_for_bill(str(row.get("status") or ""), chip or "none")
        elec_values.append(
            [
                "History",
                row.get("name") or "",
                row.get("room") or "",
                row.get("ca_no") or "",
                row.get("status") or "",
                row.get("amount") if row.get("amount") is not None else "",
                row.get("due_date") or "",
                row.get("exact_bill") if row.get("exact_bill") is not None else "",
                row.get("bill_no") or "",
                row.get("bill_date") or "",
                row.get("consumer_name") or "",
                format_ist(row.get("checked_at")),
                row.get("note") or "",
            ]
        )
        elec_tones.append(tone)
    _write_sheet(elec, elec_headers, elec_values, elec_tones, money_cols={6, 8}, date_cols={7, 10})

    notes = book.create_sheet("Notifications")
    items = build_notifications(db, today)
    note_headers = ["Category", "Title", "Tenant", "Room", "Amount", "Due date", "Days", "Read", "Detail"]
    note_values = []
    note_tones = []
    for item in items:
        tone = "error" if "error" in item["category"] else "due" if "overdue" in item["category"] or "unpaid" in item["category"] else "upcoming"
        note_values.append(
            [
                item["category"],
                item["title"],
                item["name"],
                item["room"],
                item.get("amount") if item.get("amount") is not None else "",
                item.get("due_date") or "",
                item.get("days") if item.get("days") is not None else "",
                "Yes" if item.get("read") else "No",
                item.get("body") or "",
            ]
        )
        note_tones.append(tone)
    _write_sheet(notes, note_headers, note_values, note_tones, money_cols={5}, date_cols={6})

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def build_template() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Tenants"
    headers = ["Name", "Room / flat", "Phone", "Monthly rent", "Due day", "CA number", "Notes", "Status"]
    example = ["Sample A", "Lane 1", "9811111111", 10000, "18TH", "60000000001", "Optional note", ""]
    _write_sheet(sheet, headers, [example], ["none"], money_cols={4})
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
