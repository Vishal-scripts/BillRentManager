"""Parse the Tata Power-DDL pay-bill form and the bill result page.

The live result after a correct captcha is a BILL DETAILS table: CA number,
name, bill number, bill date, due date, exact bill, and outstanding amount.
Outstanding is the amount due. Exact bill is kept separately. Payment logos
on that page (Net Banking, SBI Unipay, BillDesk) are not a payment, and this
parser never treats them as one.

A page that has that table is read strictly. If any of those fields is
missing, inconsistent, or not a clean value, the result is Error and no
amount is returned. Older synthetic fixtures that are not a BILL DETAILS
page still use the earlier label scan.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser


AMOUNT_LABELS = (
    "amount payable",
    "net payable",
    "outstanding amount",
    "payable amount",
    "current bill amount",
    "bill amount",
)

DUE_LABELS = ("due date", "bill due date", "pay by date", "pay by")
NAME_LABELS = ("consumer name", "customer name", "name of consumer")

_CAPTCHA_HINTS = (
    "correct addition",
    "invalid captcha",
    "wrong captcha",
    "verification code",
    "enter addition of two numbers",
    "captcha mismatch",
)
_INVALID_CA_HINTS = (
    "invalid ca",
    "ca number does not exist",
    "enter valid ca",
    "no such ca",
    "consumer not found",
    "invalid consumer",
)
_PAID_HINTS = (
    "no dues",
    "no outstanding",
    "nothing to pay",
    "zero balance",
    "already paid",
    "no amount payable",
)
_SITE_ERROR_HINTS = (
    "stderr.aspx",
    "technical issue",
    "services will not be available",
    "aspxerrorpath",
)

_GATEWAY_HINTS = (
    "billdesk",
    "payu",
    "razorpay",
    "ccavenue",
    "card number",
    "cvv",
    "net banking",
)

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

# Whole-label matches after case, whitespace, and periods are removed.
_LABELS = {
    "cano": "ca",
    "canumber": "ca",
    "consumerno": "ca",
    "name": "name",
    "consumername": "name",
    "customername": "name",
    "nameofconsumer": "name",
    "billno/billdate": "bill_no_date",
    "billnumber/billdate": "bill_no_date",
    "billnoandbilldate": "bill_no_date",
    "billno": "bill_no",
    "billnumber": "bill_no",
    "billdate": "bill_date",
    "duedate": "due",
    "billduedate": "due",
    "paybydate": "due",
    "payby": "due",
    "exactbill": "exact",
    "exactbillamount": "exact",
    "outstandingamount": "outstanding",
    "outstandingamt": "outstanding",
    "totaloutstanding": "outstanding",
    "netoutstanding": "outstanding",
    "outstanding": "outstanding",
    "paymentdate": "history_date",
    "lastpaymentdate": "history_date",
    "paidon": "history_date",
    "dateofpayment": "history_date",
    "lastpaidon": "history_date",
    "paymentamount": "history_amount",
    "lastpaymentamount": "history_amount",
    "amountpaid": "history_amount",
    "lastpayment": "history_amount",
}

_FIELD_ORDER = (
    ("ca", "CA number"),
    ("name", "name"),
    ("bill_no", "bill number"),
    ("bill_date", "bill date"),
    ("due", "due date"),
    ("exact", "exact bill"),
    ("outstanding", "outstanding amount"),
)


@dataclass
class BillParse:
    status: str
    amount: float | None = None
    due_date: str | None = None
    consumer_name: str | None = None
    note: str = ""
    captcha_error: bool = False
    invalid_ca: bool = False
    gateway: bool = False
    bill_no: str | None = None
    bill_date: str | None = None
    exact_bill: float | None = None
    page_ca: str | None = None
    last_payment: float | None = None
    last_paid_on: str | None = None
    save_debug: bool = False
    site_error: bool = False


SITE_ERROR_NOTE = "Tata Power site returned an error page"


def bill_parse_verified(parsed: BillParse, expected_ca: str | None = None) -> bool:
    """True when a Paid/Unpaid page has CA, amount, due date, and bill number."""
    if parsed.status not in ("Paid", "Unpaid"):
        return True
    if parsed.amount is None or parsed.exact_bill is None:
        return False
    if not parsed.due_date or not parsed.bill_no:
        return False
    if expected_ca and parsed.page_ca and parsed.page_ca != expected_ca:
        return False
    return True


def site_error_html(html: str, url: str = "") -> bool:
    blob = ((url or "") + " " + (html or "")[:12000]).lower()
    if "stderr.aspx" in blob:
        return True
    if "technical issue" in blob and "services will not be available" in blob:
        return True
    if "aspxerrorpath" in blob and "technical issue" in blob:
        return True
    return False


class _InputParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.inputs: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "input":
            return
        data = {k.lower(): (v or "") for k, v in attrs}
        name = data.get("name") or ""
        if not name:
            return
        self.inputs.append((name, data.get("value") or ""))


class _DefinitionListParser(HTMLParser):
    """Read ``dl`` / ``dt`` / ``dd`` rows used on selectpaynew.aspx."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pairs: list[tuple[str, str]] = []
        self._in_dt = False
        self._in_dd = False
        self._dt: list[str] = []
        self._dd: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name in ("script", "style"):
            self._skip += 1
            return
        if self._skip:
            return
        if name == "dt":
            self._in_dt = True
            self._dt = []
        elif name == "dd":
            self._in_dd = True
            self._dd = []

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in ("script", "style") and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if name == "dt":
            self._in_dt = False
        elif name == "dd":
            self._in_dd = False
            label = _clean_cell("".join(self._dt))
            value = _clean_cell("".join(self._dd))
            if label and value and _label_kind(label):
                self.pairs.append((label, value))

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_dt:
            self._dt.append(data)
        elif self._in_dd:
            self._dd.append(data)


def _dl_pairs(html: str) -> list[tuple[str, str]]:
    parser = _DefinitionListParser()
    parser.feed(html)
    parser.close()
    return parser.pairs


class _TableParser(HTMLParser):
    """Read two-column rows. Script and style text is ignored."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name in ("script", "style"):
            self._skip += 1
            return
        if self._skip:
            return
        if name == "tr":
            self._row = []
        elif name in ("td", "th"):
            self._cell = []
        elif name == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in ("script", "style") and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if name in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(_clean_cell("".join(self._cell)))
            self._cell = None
        elif name == "tr" and self._row is not None:
            if any(cell.strip() for cell in self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._skip or self._cell is None:
            return
        self._cell.append(data)


def parse_form_fields(html: str) -> dict[str, str]:
    parser = _InputParser()
    parser.feed(html)
    parser.close()
    fields: dict[str, str] = {}
    for name, value in parser.inputs:
        fields[name] = value
    return fields


def visible_text(html: str) -> str:
    text = re.sub(r"(?is)<script\b.*?>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style\b.*?>.*?</style>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h\d|td|th)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return text


def addition_from_text(text: str) -> int | None:
    """Read '8 + 5' (or OCR of that image) and return the sum."""
    if not text:
        return None
    cleaned = text.replace("−", "-").replace("–", "-")
    match = re.search(r"(\d{1,3})\s*\+\s*(\d{1,3})", cleaned)
    if match:
        return int(match.group(1)) + int(match.group(2))
    nums = re.findall(r"\d{1,3}", cleaned)
    if len(nums) >= 2:
        return int(nums[0]) + int(nums[1])
    return None


def _clean_cell(text: str) -> str:
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    return text.strip()


def _norm_label(label: str) -> str:
    text = label.lower().replace("\xa0", " ")
    text = re.sub(r"\s+", "", text)
    text = text.replace(".", "")
    return text.strip(":;|-")


def _label_kind(label: str) -> str | None:
    return _LABELS.get(_norm_label(label))


def _table_pairs(html: str) -> list[tuple[str, str]]:
    parser = _TableParser()
    parser.feed(html)
    parser.close()
    pairs: list[tuple[str, str]] = []
    for row in parser.rows:
        cells = [cell for cell in row if cell.strip()]
        if len(cells) >= 2 and _label_kind(cells[0]):
            pairs.append((cells[0], " ".join(cells[1:]).strip()))
            continue
        if len(cells) == 1:
            split = _split_labeled(cells[0])
            if split:
                pairs.append(split)
    return pairs


def _split_labeled(cell: str) -> tuple[str, str] | None:
    if ":" not in cell:
        return None
    left, right = cell.split(":", 1)
    if _label_kind(left) and right.strip():
        return left.strip(), right.strip()
    return None


def _text_pairs(text: str) -> list[tuple[str, str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    pairs: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(.{2,60}?)\s*:\s*(\S.*?)$", line)
        if match and _label_kind(match.group(1)):
            pairs.append((match.group(1).strip(), match.group(2).strip()))
            continue
        if _label_kind(line) and index + 1 < len(lines) and not _label_kind(lines[index + 1]):
            pairs.append((line, lines[index + 1]))
    return pairs


def _collect_pairs(html: str, text: str) -> list[tuple[str, str]]:
    pairs = _dl_pairs(html)
    seen = {_label_kind(label) for label, _value in pairs}
    for label, value in _table_pairs(html):
        kind = _label_kind(label)
        if kind and kind not in seen:
            pairs.append((label, value))
            seen.add(kind)
    for label, value in _text_pairs(text):
        kind = _label_kind(label)
        if kind and kind not in seen:
            pairs.append((label, value))
            seen.add(kind)
    return pairs


def _parse_ca(raw: str) -> str | None:
    text = raw.replace("\xa0", " ").strip()
    if not text or not re.fullmatch(r"[\d\s-]+", text):
        return None
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11:
        return digits
    return None


def _eleven(value: str | None) -> str | None:
    if value is None:
        return None
    return _parse_ca(str(value).strip()) or (
        value if re.fullmatch(r"\d{11}", str(value).strip()) else None
    )


def _parse_money(raw: str) -> float | None:
    """Accept 'Rs. 1,310.00', 'Rs. ₹ 0', and '₹ 0'. Reject ids and prose."""
    text = raw.replace("\xa0", " ").replace("₹", " ")
    # "Rs." has a dot, so a trailing word-boundary would not match.
    text = re.sub(r"(?i)\brs\.?", " ", text)
    text = re.sub(r"(?i)\binr\b", " ", text)
    text = text.replace("/-", " ")
    text = re.sub(r"\s+", " ", text).strip().rstrip("+-")
    match = re.fullmatch(r"(\d{1,3}(?:,\d{2,3})+|\d{1,7})(\.\d{1,2})?", text)
    if not match:
        return None
    whole = match.group(1).replace(",", "")
    if len(whole) > 7:
        return None
    try:
        return float(whole + (match.group(2) or ""))
    except ValueError:
        return None


def _normalize_date(raw: str) -> str | None:
    text = raw.strip()
    named = re.fullmatch(r"(\d{1,2})[-/]([A-Za-z]{3,9})[-/](\d{4})", text)
    if named:
        month = _MONTHS.get(named.group(2).lower())
        if not month:
            return None
        try:
            return date(int(named.group(3)), month, int(named.group(1))).isoformat()
        except ValueError:
            return None
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%y", "%d/%m/%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _date_value(raw: str) -> str | None:
    text = re.sub(r"\s+", " ", raw).strip()
    if not re.fullmatch(
        r"\d{1,2}[-/](?:\d{1,2}|[A-Za-z]{3,9})[-/]\d{2,4}|\d{4}-\d{2}-\d{2}",
        text,
    ):
        return None
    return _normalize_date(text)


def _parse_bill_no(raw: str) -> str | None:
    text = raw.strip()
    match = re.fullmatch(r"\d{8,16}", text)
    if not match:
        return None
    return match.group(0)


def _split_bill_no_date(raw: str) -> tuple[str | None, str | None]:
    text = re.sub(r"\s+", " ", raw).strip()
    match = re.fullmatch(
        r"(\d{8,16})\s*/\s*(\d{1,2}[-/](?:\d{1,2}|[A-Za-z]{3,9})[-/]\d{2,4})",
        text,
    )
    if not match:
        return None, None
    return match.group(1), _normalize_date(match.group(2))


def _clean_name(raw: str) -> str | None:
    text = raw.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text or len(text) > 80:
        return None
    if re.fullmatch(r"[\d\s./,-]+", text):
        return None
    if _label_kind(text):
        return None
    return text


def _labeled_ca(pairs: list[tuple[str, str]]) -> str | None:
    for label, value in pairs:
        if _label_kind(label) == "ca":
            return _parse_ca(value)
    return None


def _confirmation_ca(text: str) -> str | None:
    flat = re.sub(r"\s+", " ", text)
    match = re.search(
        r"payment is being done against ca\s*no\s*:?\s*(\d{11})",
        flat,
        re.I,
    )
    if not match:
        return None
    return match.group(1)


def _is_bill_details(text: str, pairs: list[tuple[str, str]]) -> bool:
    if "bill details" in text.lower():
        return True
    kinds = {_label_kind(label) for label, _value in pairs}
    return {"ca", "exact", "outstanding"} <= kinds


def _values_from_pairs(pairs: list[tuple[str, str]]) -> dict[str, str]:
    found: dict[str, str] = {}
    for label, value in pairs:
        kind = _label_kind(label)
        if kind and kind not in found:
            found[kind] = value
    return found


def _ca_mismatch(page_ca: str, expected_ca: str) -> BillParse:
    return BillParse(
        status="Error",
        note=(
            f"CA on the result page ({page_ca}) does not match the requested "
            f"CA ({expected_ca}). Nothing was written."
        ),
        page_ca=page_ca,
        save_debug=True,
    )


def _parse_bill_details(
    text: str,
    pairs: list[tuple[str, str]],
    expected_ca: str | None,
) -> BillParse:
    found = _values_from_pairs(pairs)
    page_ca = _parse_ca(found["ca"]) if "ca" in found else None
    name = _clean_name(found["name"]) if "name" in found else None
    due = _date_value(found["due"]) if "due" in found else None
    exact = _parse_money(found["exact"]) if "exact" in found else None
    outstanding = _parse_money(found["outstanding"]) if "outstanding" in found else None
    bill_no = None
    bill_date = None
    if "bill_no_date" in found:
        bill_no, bill_date = _split_bill_no_date(found["bill_no_date"])
    if bill_no is None and "bill_no" in found:
        bill_no = _parse_bill_no(found["bill_no"])
    if bill_date is None and "bill_date" in found:
        bill_date = _date_value(found["bill_date"])

    confirm = _confirmation_ca(text)
    if page_ca and confirm and confirm != page_ca:
        return BillParse(
            status="Error",
            note=(
                f"CA on the result page is inconsistent (table {page_ca}, "
                f"confirmation {confirm}). Nothing was written."
            ),
            save_debug=True,
        )

    parsed = {
        "ca": page_ca,
        "name": name,
        "bill_no": bill_no,
        "bill_date": bill_date,
        "due": due,
        "exact": exact,
        "outstanding": outstanding,
    }
    missing = [label for key, label in _FIELD_ORDER if parsed[key] is None]
    if missing:
        return BillParse(
            status="Error",
            note="Could not read: " + ", ".join(missing) + ".",
            save_debug=True,
        )

    requested = _eleven(expected_ca) if expected_ca else None
    if requested and page_ca != requested:
        return _ca_mismatch(page_ca or "", requested)

    assert outstanding is not None
    if outstanding == 0:
        status = "Paid"
        amount = 0.0
    elif outstanding > 0:
        status = "Unpaid"
        amount = outstanding
    else:
        return BillParse(
            status="Error",
            note="Could not read: outstanding amount.",
            save_debug=True,
        )
    return BillParse(
        status=status,
        amount=amount,
        due_date=due,
        consumer_name=name,
        bill_no=bill_no,
        bill_date=bill_date,
        exact_bill=exact,
        page_ca=page_ca,
    )


def _amount_after(text: str, label: str) -> float | None:
    pattern = re.compile(re.escape(label), re.I)
    for match in pattern.finditer(text):
        window = text[match.end() : match.end() + 80]
        number = re.search(
            r"(?<!\d)(\d{1,3}(?:,\d{2,3})+|\d{1,7})(\.\d{1,2})?(?!\d)",
            window,
        )
        if not number:
            continue
        raw = (number.group(1) + (number.group(2) or "")).replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        if value > 5_000_000:
            continue
        return value
    return None


def _date_after(text: str, label: str) -> str | None:
    pattern = re.compile(re.escape(label), re.I)
    date_re = re.compile(
        r"(\d{1,2}[-/](?:\d{1,2}|[A-Za-z]{3,9})[-/]\d{2,4}|\d{4}-\d{2}-\d{2})"
    )
    for match in pattern.finditer(text):
        window = text[match.end() : match.end() + 40]
        found = date_re.search(window)
        if not found:
            continue
        normalized = _normalize_date(found.group(1))
        if normalized:
            return normalized
    return None


def _name_after(text: str, label: str) -> str | None:
    pattern = re.compile(re.escape(label), re.I)
    match = pattern.search(text)
    if not match:
        return None
    window = text[match.end() : match.end() + 80]
    for line in window.split("\n"):
        line = line.replace(":", " ").strip(" :-")
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) >= 2:
            return line[:80]
    return None


def parse_payment_history(html: str, expected_ca: str | None = None) -> tuple[float | None, str | None]:
    """Last payment amount and date, only when both labels are exact.

    A missing or unclear history page returns (None, None). It is not a
    guess, and the caller must not fail the bill because of it.
    """
    text = visible_text(html)
    pairs = _collect_pairs(html, text)
    page_ca = _labeled_ca(pairs)
    requested = _eleven(expected_ca) if expected_ca else None
    if requested and page_ca and page_ca != requested:
        return None, None
    amount_raw = None
    date_raw = None
    for label, value in pairs:
        kind = _label_kind(label)
        if kind == "history_amount" and amount_raw is None:
            amount_raw = value
        elif kind == "history_date" and date_raw is None:
            date_raw = value
    if amount_raw is None or date_raw is None:
        return None, None
    amount = _parse_money(amount_raw)
    paid_on = _date_value(date_raw)
    if amount is None or paid_on is None:
        return None, None
    return amount, paid_on


def parse_bill_html(html: str, expected_ca: str | None = None) -> BillParse:
    if site_error_html(html):
        return BillParse(status="Error", note=SITE_ERROR_NOTE, site_error=True)
    text = visible_text(html)
    pairs = _collect_pairs(html, text)
    if _is_bill_details(text, pairs):
        return _parse_bill_details(text, pairs, expected_ca)

    page_ca = _labeled_ca(pairs)
    requested = _eleven(expected_ca) if expected_ca else None
    if requested and page_ca and page_ca != requested:
        return _ca_mismatch(page_ca, requested)

    lowered = text.lower()
    on_pay_form = "txtcano" in html.lower() and "paybillonline" in html.lower()
    gateway = False if on_pay_form else any(hint in lowered for hint in _GATEWAY_HINTS)

    amount = None
    for label in AMOUNT_LABELS:
        amount = _amount_after(text, label)
        if amount is not None:
            break
    due = None
    for label in DUE_LABELS:
        due = _date_after(text, label)
        if due:
            break
    name = None
    for label in NAME_LABELS:
        name = _name_after(text, label)
        if name:
            break

    if amount is not None and not gateway:
        status = "Paid" if amount == 0 else "Unpaid"
        note = "Zero balance" if amount == 0 else ""
        return BillParse(
            status=status,
            amount=amount,
            due_date=due,
            consumer_name=name,
            note=note,
            page_ca=page_ca,
        )

    if amount is not None and gateway:
        # Amount was on the page. Do not follow the payment button.
        status = "Paid" if amount == 0 else "Unpaid"
        return BillParse(
            status=status,
            amount=amount,
            due_date=due,
            consumer_name=name,
            note="Payment button was present and was not clicked",
            gateway=True,
            page_ca=page_ca,
        )

    if any(hint in lowered for hint in _PAID_HINTS):
        return BillParse(
            status="Paid",
            amount=0.0,
            due_date=due,
            consumer_name=name,
            note="No dues text",
            page_ca=page_ca,
        )

    captcha = any(hint in lowered for hint in _CAPTCHA_HINTS)
    invalid = any(hint in lowered for hint in _INVALID_CA_HINTS)
    if gateway and amount is None:
        return BillParse(
            status="Error",
            note="Stopped before the payment page. No bill amount was shown.",
            gateway=True,
        )
    if captcha:
        return BillParse(status="Error", note="Captcha was rejected", captcha_error=True)
    if invalid:
        return BillParse(status="Error", note="CA number was rejected", invalid_ca=True)
    return BillParse(
        status="Error",
        note="Could not find a bill amount on the page",
        save_debug=True,
    )
