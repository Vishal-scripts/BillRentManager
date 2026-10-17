"""Parser, captcha, and seed-data tests. No network."""

from __future__ import annotations

import csv
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from billcheck.parsers import (  # noqa: E402
    SITE_ERROR_NOTE,
    addition_from_text,
    parse_bill_html,
    parse_form_fields,
    parse_payment_history,
    site_error_html,
)
from billcheck.captcha import ocr_addition  # noqa: E402
from billcheck.check_bills import format_summary, post_webapp, summarize  # noqa: E402


FIXTURES = ROOT / "billcheck" / "fixtures"


class ParserTests(unittest.TestCase):
    def test_selectpay_dl_layout_reads_exact_and_outstanding(self):
        html = """
        <html><body><h2>BILL DETAILS</h2>
        <dl class="payment-details">
          <dt>CA NO.</dt><dd><span>60099990001</span></dd>
          <dt>Name :</dt><dd>Mr. SAMPLE OWNER</dd>
          <dt>Bill No./Bill Date :</dt><dd>010614251296 / 28-Jul-2026</dd>
          <dt>Due Date :</dt><dd>12-Aug-2026</dd>
          <dt>Exact Bill :</dt><dd>Rs. <span>7230.00</span></dd>
          <dt>Outstanding Amount :</dt><dd>Rs. <span>₹ 0</span></dd>
        </dl>
        </body></html>
        """
        parsed = parse_bill_html(html, expected_ca="60099990001")
        self.assertEqual(parsed.status, "Paid")
        self.assertEqual(parsed.amount, 0.0)
        self.assertEqual(parsed.exact_bill, 7230.0)
        self.assertEqual(parsed.due_date, "2026-08-12")

    def test_stderr_page_is_not_reported_as_missing_amount(self):
        html = (ROOT / "billcheck" / "fixtures" / "site_error_stderr.html").read_text(encoding="utf-8")
        self.assertTrue(site_error_html(html))
        parsed = parse_bill_html(html, expected_ca="60099990001")
        self.assertTrue(parsed.site_error)
        self.assertEqual(parsed.note, SITE_ERROR_NOTE)
        self.assertNotIn("Could not find a bill amount", parsed.note)

    def test_form_fields_match_the_archived_pay_page(self):
        html = (FIXTURES / "form_paybillonline.html").read_text(encoding="utf-8")
        fields = parse_form_fields(html)
        for name in ("txtcano", "txtmobile", "txtemail", "TxtImgVer", "btnpay", "__VIEWSTATE"):
            self.assertIn(name, fields)
        self.assertEqual(fields["btnpay"], "Pay Now")
        self.assertIn("capt.aspx", html)

    def test_unpaid_prefers_amount_payable_and_due_date(self):
        parsed = parse_bill_html((FIXTURES / "result_unpaid.html").read_text(encoding="utf-8"))
        self.assertEqual(parsed.status, "Unpaid")
        self.assertEqual(parsed.amount, 1234.50)
        self.assertEqual(parsed.due_date, "2026-10-18")
        self.assertEqual(parsed.consumer_name, "SAMPLE TENANT A")
        self.assertFalse(parsed.gateway)

    def test_zero_outstanding_is_paid(self):
        parsed = parse_bill_html((FIXTURES / "result_paid.html").read_text(encoding="utf-8"))
        self.assertEqual(parsed.status, "Paid")
        self.assertEqual(parsed.amount, 0.0)
        self.assertEqual(parsed.due_date, "2026-09-01")

    def test_no_dues_text(self):
        parsed = parse_bill_html((FIXTURES / "result_nodues.html").read_text(encoding="utf-8"))
        self.assertEqual(parsed.status, "Paid")
        self.assertEqual(parsed.amount, 0.0)

    def test_captcha_and_invalid_ca(self):
        captcha = parse_bill_html((FIXTURES / "result_captcha.html").read_text(encoding="utf-8"))
        self.assertTrue(captcha.captcha_error)
        self.assertEqual(captcha.status, "Error")
        invalid = parse_bill_html((FIXTURES / "result_invalid.html").read_text(encoding="utf-8"))
        self.assertTrue(invalid.invalid_ca)
        self.assertEqual(invalid.status, "Error")

    def test_payment_gateway_is_not_treated_as_a_bill(self):
        parsed = parse_bill_html((FIXTURES / "result_gateway.html").read_text(encoding="utf-8"))
        self.assertEqual(parsed.status, "Error")
        self.assertTrue(parsed.gateway)
        self.assertIsNone(parsed.amount)
        self.assertIn("stopped", parsed.note.lower())

    def test_bill_details_page_matches_the_screenshot(self):
        html = (FIXTURES / "result_bill_details.html").read_text(encoding="utf-8")
        parsed = parse_bill_html(html, expected_ca="60099990002")
        self.assertEqual(parsed.status, "Paid")
        self.assertEqual(parsed.page_ca, "60099990002")
        self.assertEqual(parsed.consumer_name, "Ms. SAROJ .")
        self.assertEqual(parsed.bill_no, "010214833626")
        self.assertTrue(parsed.bill_no.startswith("0"))
        self.assertEqual(parsed.bill_date, "2026-10-01")
        self.assertEqual(parsed.due_date, "2026-10-16")
        self.assertEqual(parsed.exact_bill, 1310.00)
        self.assertEqual(parsed.amount, 0.0)
        self.assertFalse(parsed.gateway)
        self.assertNotIn("stopped", parsed.note.lower())
        self.assertIn("BillDesk", html)
        self.assertIn("Net Banking", html)

    def test_bill_details_labels_ignore_case_and_whitespace(self):
        html = (FIXTURES / "result_bill_details.html").read_text(encoding="utf-8")
        html = html.replace("CA NO.", "ca   no.")
        html = html.replace("Name :", "NAME:")
        html = html.replace("Bill No./Bill Date :", "Bill No. / Bill Date")
        html = html.replace("Due Date :", "due date")
        html = html.replace("Exact Bill :", "EXACT BILL")
        html = html.replace("Outstanding Amount :", "outstanding   amount")
        parsed = parse_bill_html(html, expected_ca="60099990002")
        self.assertEqual(parsed.status, "Paid")
        self.assertEqual(parsed.consumer_name, "Ms. SAROJ .")
        self.assertEqual(parsed.bill_no, "010214833626")
        self.assertEqual(parsed.bill_date, "2026-10-01")
        self.assertEqual(parsed.due_date, "2026-10-16")
        self.assertEqual(parsed.exact_bill, 1310.00)
        self.assertEqual(parsed.amount, 0.0)

    def test_outstanding_above_zero_is_unpaid_and_keeps_exact_bill(self):
        html = (FIXTURES / "result_bill_details.html").read_text(encoding="utf-8")
        html = html.replace("Rs. ₹ 0", "Rs. 1,310.50")
        parsed = parse_bill_html(html, expected_ca="60099990002")
        self.assertEqual(parsed.status, "Unpaid")
        self.assertEqual(parsed.amount, 1310.50)
        self.assertEqual(parsed.exact_bill, 1310.00)
        self.assertEqual(parsed.bill_no, "010214833626")

    def test_rupee_entity_zero_is_paid(self):
        html = (FIXTURES / "result_bill_details.html").read_text(encoding="utf-8")
        html = html.replace("Rs. ₹ 0", "Rs. &#8377; 0")
        self.assertIn("&#8377;", html)
        parsed = parse_bill_html(html, expected_ca="60099990002")
        self.assertEqual(parsed.status, "Paid")
        self.assertEqual(parsed.amount, 0.0)
        self.assertEqual(parsed.exact_bill, 1310.00)

    def test_ca_on_the_page_must_match_before_anything_is_written(self):
        html = (FIXTURES / "result_bill_details.html").read_text(encoding="utf-8")
        parsed = parse_bill_html(html, expected_ca="60000000001")
        self.assertEqual(parsed.status, "Error")
        self.assertIsNone(parsed.amount)
        self.assertIsNone(parsed.exact_bill)
        self.assertIsNone(parsed.bill_no)
        self.assertIn("60099990002", parsed.note)
        self.assertIn("60000000001", parsed.note)
        self.assertIn("does not match", parsed.note)

    def test_inconsistent_confirmation_ca_is_not_written(self):
        html = (FIXTURES / "result_bill_details.html").read_text(encoding="utf-8")
        html = html.replace("CA NO:60099990002", "CA NO:60000000000")
        parsed = parse_bill_html(html, expected_ca="60099990002")
        self.assertEqual(parsed.status, "Error")
        self.assertIsNone(parsed.amount)
        self.assertIn("inconsistent", parsed.note)

    def test_missing_outstanding_is_error_and_does_not_use_the_amount_box(self):
        html = """
        <html><body>
        <h2>BILL DETAILS</h2>
        <table>
          <tr><td>CA NO.</td><td>60099990002</td></tr>
          <tr><td>Name :</td><td>Ms. SAROJ .</td></tr>
          <tr><td>Bill No./Bill Date :</td><td>010214833626 / 1-Oct-2026</td></tr>
          <tr><td>Due Date :</td><td>16-Oct-2026</td></tr>
          <tr><td>Exact Bill :</td><td>Rs. 1310.00</td></tr>
        </table>
        <h3>Net Banking</h3>
        <img alt="BillDesk" />
        <input value="₹ 0" />
        </body></html>
        """
        parsed = parse_bill_html(html, expected_ca="60099990002")
        self.assertEqual(parsed.status, "Error")
        self.assertIsNone(parsed.amount)
        self.assertIsNone(parsed.exact_bill)
        self.assertIn("outstanding amount", parsed.note)
        self.assertTrue(parsed.save_debug)

    def test_last_payment_history_needs_both_amount_and_date(self):
        html = (FIXTURES / "result_payment_history.html").read_text(encoding="utf-8")
        amount, paid_on = parse_payment_history(html, expected_ca="60099990002")
        self.assertEqual(amount, 980.00)
        self.assertEqual(paid_on, "2026-10-02")
        self.assertEqual(parse_payment_history(html, expected_ca="60000000001"), (None, None))
        partial = html.replace("Rs. 980.00", "see cashier")
        self.assertEqual(parse_payment_history(partial, expected_ca="60099990002"), (None, None))

    def test_labeled_ca_on_an_older_page_must_still_match(self):
        html = (FIXTURES / "result_unpaid.html").read_text(encoding="utf-8")
        mismatch = parse_bill_html(html, expected_ca="60000000001")
        self.assertEqual(mismatch.status, "Error")
        self.assertIsNone(mismatch.amount)
        matched = parse_bill_html(html, expected_ca="60099990003")
        self.assertEqual(matched.status, "Unpaid")
        self.assertEqual(matched.amount, 1234.50)

    def test_addition_text(self):
        self.assertEqual(addition_from_text("8 + 5"), 13)
        self.assertEqual(addition_from_text("12+7="), 19)
        self.assertEqual(addition_from_text("noise 3 + 4 ?"), 7)
        self.assertIsNone(addition_from_text("no numbers"))

    def test_ocr_reads_a_simple_addition_image(self):
        image = Image.new("RGB", (280, 80), "white")
        draw = ImageDraw.Draw(image)
        font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        font = ImageFont.truetype(str(font_path), 42) if font_path.exists() else ImageFont.load_default()
        draw.text((20, 15), "8 + 5", fill="black", font=font)
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        self.assertEqual(ocr_addition(buf.getvalue()), 13)


class _SheetResponse:
    def __init__(self, status=200, payload=None, headers=None, text=""):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._payload


class SheetRetryTests(unittest.TestCase):
    def test_network_errors_retry_with_backoff_then_succeed(self):
        calls = {"n": 0}
        waits = []

        def post(url, json, timeout, allow_redirects):
            calls["n"] += 1
            if calls["n"] < 3:
                raise requests.ConnectionError("reset")
            return _SheetResponse(200, {"ok": True, "updated": 1})

        with mock.patch("billcheck.check_bills.requests.post", post):
            body = post_webapp("https://script.example/exec", {"a": 1}, pause=waits.append)
        self.assertTrue(body["ok"])
        self.assertEqual(calls["n"], 3)
        self.assertEqual(waits, [0.5, 1.0])

    def test_http_500_is_retried_and_a_rejected_token_is_not(self):
        calls = {"n": 0}

        def post(url, json, timeout, allow_redirects):
            calls["n"] += 1
            if calls["n"] == 1:
                return _SheetResponse(500, text="busy")
            return _SheetResponse(200, {"ok": True})

        with mock.patch("billcheck.check_bills.requests.post", post):
            body = post_webapp("https://script.example/exec", {"a": 1}, pause=lambda _s: None)
        self.assertTrue(body["ok"])
        self.assertEqual(calls["n"], 2)

        calls["n"] = 0

        def denied(url, json, timeout, allow_redirects):
            calls["n"] += 1
            return _SheetResponse(200, {"ok": False, "error": "Invalid token"})

        with mock.patch("billcheck.check_bills.requests.post", denied):
            body = post_webapp("https://script.example/exec", {"a": 1}, pause=lambda _s: None)
        self.assertFalse(body["ok"])
        self.assertEqual(calls["n"], 1)
        self.assertIn("Invalid token", body["error"])

    def test_redirect_keeps_the_json_body(self):
        seen = []

        def post(url, json, timeout, allow_redirects):
            seen.append(url)
            if len(seen) == 1:
                return _SheetResponse(302, headers={"Location": "https://script.google.com/macros/echo"})
            return _SheetResponse(200, {"ok": True, "updated": 2})

        with mock.patch("billcheck.check_bills.requests.post", post):
            body = post_webapp("https://script.example/exec", {"token": "t"}, pause=lambda _s: None)
        self.assertEqual(body["updated"], 2)
        self.assertEqual(seen[1], "https://script.google.com/macros/echo")

    def test_summary_counts(self):
        summary = summarize([
            {"status": "Paid"},
            {"status": "Paid"},
            {"status": "Unpaid"},
            {"status": "Error"},
        ])
        self.assertEqual(summary, {"ok": 3, "paid": 2, "unpaid": 1, "errors": 1})
        self.assertEqual(format_summary(summary), "Run summary: ok=3 paid=2 unpaid=1 errors=1")


class SeedTests(unittest.TestCase):
    def test_seed_matches_the_pdf_total(self):
        path = ROOT / "data" / "tenants.csv"
        if not path.is_file():
            self.skipTest("data/tenants.csv stays on this computer and is not in the source archive")
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 45)
        rent = 0
        cas = []
        phones = 0
        remindable = 0
        for row in rows:
            self.assertNotEqual(row["S.NO"], "46")
            if row["RENT"]:
                rent += int(row["RENT"])
            if row["CA.NO"]:
                self.assertEqual(len(row["CA.NO"]), 11, row["S.NO"])
                cas.append(row["CA.NO"])
            phone = row["PH. NO"]
            if phone:
                self.assertTrue(phone.isdigit(), row["S.NO"])
            if len(phone) == 10 and row["RENT"] and row["DATE"]:
                phones += 1
                remindable += 1
        self.assertEqual(rent, 289200)
        self.assertEqual(len(cas), 41)
        self.assertEqual(len(set(cas)), 41)
        self.assertEqual(remindable, 31)
        # The 9-digit number from the name column is kept, and is not a sendable phone.
        row37 = next(row for row in rows if row["S.NO"] == "37")
        self.assertEqual(row37["PH. NO"], "987108407")
        self.assertEqual(row37["NAME"], "")
        self.assertIn("9 digits", row37["NOTES"])
        self.assertEqual(next(row for row in rows if row["S.NO"] == "10")["NAME"], "SCHOOL.")


if __name__ == "__main__":
    unittest.main()
