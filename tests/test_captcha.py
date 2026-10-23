"""Captcha OCR and bill-client tests. No network."""

from __future__ import annotations

import csv
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import requests

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from billcheck.captcha import captcha_tries_from_env, ocr_addition, operands_from_ocr, read_operands  # noqa: E402
from billcheck.check_bills import captcha_retry_needed, main  # noqa: E402
from billcheck.ndpl import NdplClient  # noqa: E402


FIXTURES = ROOT / "billcheck" / "fixtures"
BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
REGULAR = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")

# Same pairs the accuracy note in docs/BILL_CHECK.md was measured on.
_PAIRS = (
    (16, 22), (8, 5), (1, 1), (9, 9), (7, 13), (23, 4), (4, 20), (15, 18),
    (6, 11), (12, 7), (10, 10), (2, 14), (21, 8), (19, 3), (5, 6), (3, 30),
    (14, 2), (11, 11), (18, 9), (0, 7), (13, 17), (25, 6), (8, 15), (4, 4),
    (17, 2),
)
_COLORS = ((0, 90, 30), (0, 75, 15), (10, 100, 35), (0, 0, 0), (20, 80, 20))


def _font(path: Path, size: int) -> ImageFont.ImageFont:
    if path.is_file():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _captcha(left: int, right: int, *, size: int, color, strike: int, wh: tuple[int, int], font_path: Path) -> bytes:
    """Dark-green (or black) addition image with an optional strike through the middle."""
    image = Image.new("RGB", wh, "white")
    draw = ImageDraw.Draw(image)
    font = _font(font_path, size)
    text = f"{left} + {right}"
    box = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = box[2] - box[0], box[3] - box[1]
    origin_x = max(2, (wh[0] - text_w) // 2 - box[0])
    origin_y = max(1, (wh[1] - text_h) // 2 - box[1])
    draw.text((origin_x, origin_y), text, fill=color, font=font)
    if strike:
        drawn = draw.textbbox((origin_x, origin_y), text, font=font)
        mid = (drawn[1] + drawn[3]) // 2
        draw.line((drawn[0] - 2, mid, drawn[2] + 2, mid), fill=color, width=strike)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _Response:
    def __init__(
        self,
        status: int,
        text: str = "",
        content: bytes = b"",
        url: str = "https://www.tatapower-ddl.com/billpay/paybillonline.aspx",
    ) -> None:
        self.status_code = status
        self.text = text
        self.content = content
        self.url = url

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise OSError(f"HTTP {self.status_code}")


class OperandTests(unittest.TestCase):
    def test_accepts_exactly_two_short_numbers(self):
        self.assertEqual(operands_from_ocr("16+22"), (16, 22))
        self.assertEqual(operands_from_ocr("8 + 5"), (8, 5))
        self.assertEqual(operands_from_ocr("  4+20 "), (4, 20))

    def test_rejects_guesses(self):
        for text in ("", "845", "16+22+1", "100+2", "16 22", "no numbers", "8++5"):
            self.assertIsNone(operands_from_ocr(text), text)


class SampleImageTests(unittest.TestCase):
    def test_attached_style_captcha_is_38(self):
        image = (FIXTURES / "captcha_16_plus_22.png").read_bytes()
        self.assertEqual(read_operands(image), (16, 22))
        self.assertEqual(ocr_addition(image), 38)

    def test_plain_addition_without_a_strike_still_reads(self):
        image = Image.new("RGB", (280, 80), "white")
        draw = ImageDraw.Draw(image)
        draw.text((20, 15), "8 + 5", fill="black", font=_font(REGULAR, 42))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        self.assertEqual(ocr_addition(buffer.getvalue()), 13)

    def test_blank_image_is_not_submitted_as_a_guess(self):
        image = Image.new("RGB", (220, 70), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        self.assertIsNone(ocr_addition(buffer.getvalue()))

    def test_measured_accuracy_on_strike_through_samples(self):
        if not BOLD.is_file():
            self.skipTest("DejaVu is required to draw the sample captchas")
        correct = wrong = unread = 0
        samples = []
        for index, (left, right) in enumerate(_PAIRS):
            samples.append((left, right, _captcha(
                left, right, size=42, color=_COLORS[index % 5], strike=2,
                wh=(280, 86), font_path=BOLD,
            )))
            samples.append((left, right, _captcha(
                left, right, size=34, color=_COLORS[(index + 1) % 5], strike=2,
                wh=(230, 70), font_path=REGULAR,
            )))
        samples.append((8, 5, _captcha(8, 5, size=42, color=(0, 0, 0), strike=0, wh=(280, 80), font_path=BOLD)))
        samples.append((16, 22, _captcha(
            16, 22, size=36, color=(0, 100, 20), strike=2, wh=(250, 76), font_path=BOLD,
        )))
        for left, right, image in samples:
            got = read_operands(image)
            if got == (left, right):
                correct += 1
            elif got is None:
                unread += 1
            else:
                wrong += 1
        total = len(samples)
        print(
            f"captcha accuracy: {correct}/{total} = {100 * correct / total:.1f}% "
            f"(wrong {wrong}, unread {unread})"
        )
        self.assertEqual(wrong, 0)
        self.assertGreaterEqual(correct / total, 0.70)
        self.assertEqual(correct + wrong + unread, total)


class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.form = (FIXTURES / "form_paybillonline.html").read_text(encoding="utf-8")
        self.unpaid = (FIXTURES / "result_unpaid.html").read_text(encoding="utf-8")
        self.rejected = (FIXTURES / "result_captcha.html").read_text(encoding="utf-8")
        self.image = _captcha(8, 5, size=42, color=(0, 90, 30), strike=2, wh=(280, 86), font_path=BOLD)
        self._ocr_patch = mock.patch(
            "billcheck.ndpl.solve_captcha",
            return_value=(13, {"chosen_sum": 13, "chosen_pair": (8, 5)}),
        )
        self._ocr_patch.start()

    def tearDown(self) -> None:
        self._ocr_patch.stop()

    def _client(self, tries: int = 3) -> NdplClient:
        return NdplClient(
            pay_url="https://www.tatapower-ddl.com/billpay/paybillonline.aspx",
            email="owner@example.com",
            mobile="9811111111",
            captcha_tries=tries,
            pause=lambda _seconds: None,
        )

    def test_default_tries_follow_the_environment(self):
        self.assertEqual(NdplClient().captcha_tries, 8)
        with mock.patch.dict(os.environ, {"NDPL_CAPTCHA_TRIES": "4"}):
            self.assertEqual(captcha_tries_from_env(), 4)
        with mock.patch.dict(os.environ, {"NDPL_CAPTCHA_TRIES": "nope"}):
            self.assertEqual(captcha_tries_from_env(), 8)

    def test_confident_sum_is_posted_and_the_bill_is_not_paid(self):
        client = self._client()
        posted = {}

        def get(url, timeout=None, headers=None):
            if "Img.aspx" in url or "capt.aspx" in url:
                return _Response(200, content=self.image)
            return _Response(200, text=self.form)

        def post(url, data, timeout=None, allow_redirects=True, headers=None):
            posted.update(data)
            return _Response(200, text=self.unpaid)

        client.session.get = get
        client.session.post = post
        result = client.fetch("60099990003")
        self.assertEqual(result.status, "Unpaid")
        self.assertEqual(result.amount, 1234.50)
        self.assertEqual(posted["TxtImgVer"], "13")
        self.assertEqual(posted["txtcano"], "60099990003")
        self.assertEqual(posted["btnpay"], "Pay Now")
        self.assertNotIn("cardnumber", {key.lower() for key in posted})
        self.assertNotIn("cvv", {key.lower() for key in posted})

    def test_unreadable_captcha_is_not_posted(self):
        self._ocr_patch.stop()
        self._ocr_patch = mock.patch("billcheck.ndpl.solve_captcha", return_value=(None, {"chosen_sum": None}))
        self._ocr_patch.start()
        client = self._client(tries=2)
        blank = io.BytesIO()
        Image.new("RGB", (180, 50), "white").save(blank, format="PNG")
        posts = []

        def get(url, timeout=None, headers=None):
            if "Img.aspx" in url or "capt.aspx" in url:
                return _Response(200, content=blank.getvalue())
            return _Response(200, text=self.form)

        def post(url, data, timeout=None, allow_redirects=True, headers=None):
            posts.append(data)
            return _Response(200, text=self.unpaid)

        client.session.get = get
        client.session.post = post
        result = client.fetch("60000000001")
        self.assertEqual(posts, [])
        self.assertEqual(result.status, "Error")
        self.assertIn("after 2 tries", result.note)
        self.assertIn("not clear", result.note)

    def test_rejected_captcha_is_refreshed_and_then_read(self):
        client = self._client(tries=3)
        posts = []

        def get(url, timeout=None, headers=None):
            if "Img.aspx" in url or "capt.aspx" in url:
                return _Response(200, content=self.image)
            return _Response(200, text=self.form)

        def post(url, data, timeout=None, allow_redirects=True, headers=None):
            posts.append(data["TxtImgVer"])
            if len(posts) == 1:
                return _Response(200, text=self.rejected)
            return _Response(200, text=self.unpaid)

        client.session.get = get
        client.session.post = post
        result = client.fetch("60099990003")
        self.assertEqual(result.status, "Unpaid")
        self.assertEqual(posts, ["13", "13"])

    def test_block_page_is_an_error_for_that_ca_and_is_not_retried(self):
        client = self._client()
        calls = {"n": 0}

        def get(url, timeout=None, headers=None):
            calls["n"] += 1
            return _Response(
                406,
                text="<html>Request was blocked due to suspicious behavior. custom-page.apptrana.com</html>",
            )

        client.session.get = get
        first = client.fetch("60000000001")
        second = client.fetch("60000000002")
        self.assertEqual(first.status, "Error")
        self.assertIn("406", first.note)
        self.assertIn("AppTrana", first.note)
        self.assertEqual(second.status, "Error")
        self.assertEqual(second.note, first.note)
        self.assertEqual(calls["n"], 1)

    def test_timeout_and_maintenance_are_errors(self):
        client = self._client()

        def timed_out(url, timeout=None, headers=None):
            raise requests.Timeout("slow")

        client.session.get = timed_out
        result = client.fetch("60000000001")
        self.assertEqual(result.status, "Error")
        self.assertIn("Timed out", result.note)
        self.assertEqual(client.restarts, 1)

        def down(url, timeout):
            return _Response(503, text="<html>under maintenance</html>")

        client.blocked_note = None
        client.session.get = down
        result = client.fetch("60000000001")
        self.assertEqual(result.status, "Error")
        self.assertIn("maintenance", result.note)
        self.assertIn("503", result.note)

    def test_dropped_connection_restarts_the_session_once(self):
        client = self._client()
        calls = {"n": 0}

        def get(url, timeout=None, headers=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise requests.ConnectionError("connection reset")
            if "Img.aspx" in url or "capt.aspx" in url:
                return _Response(200, content=self.image)
            return _Response(200, text=self.form)

        def post(url, data, timeout=None, allow_redirects=True, headers=None):
            return _Response(200, text=self.unpaid)

        client.session.get = get
        client.session.post = post
        result = client.fetch("60099990003")
        self.assertEqual(result.status, "Unpaid")
        self.assertEqual(client.restarts, 1)
        self.assertGreaterEqual(calls["n"], 2)

    def test_bill_details_are_read_and_payment_options_are_not_opened(self):
        client = self._client()
        details = (FIXTURES / "result_bill_details.html").read_text(encoding="utf-8")
        gets = []
        posts = []

        def get(url, timeout=None, headers=None):
            gets.append(url)
            if "Img.aspx" in url or "capt.aspx" in url:
                return _Response(200, content=self.image)
            return _Response(200, text=self.form)

        def post(url, data, timeout=None, allow_redirects=True, headers=None):
            posts.append((url, data))
            return _Response(200, text=details)

        client.session.get = get
        client.session.post = post
        result = client.fetch("60099990002")
        self.assertEqual(result.status, "Paid")
        self.assertEqual(result.amount, 0.0)
        self.assertEqual(result.exact_bill, 1310.0)
        self.assertEqual(result.bill_no, "010000000001")
        self.assertEqual(result.bill_date, "2026-10-01")
        self.assertEqual(result.due_date, "2026-10-16")
        self.assertEqual(result.consumer_name, "Ms. ASHA .")
        self.assertEqual(result.verified_ca, "60099990002")
        self.assertEqual(len(posts), 1)
        self.assertNotIn("cardnumber", {key.lower() for key in posts[0][1]})
        self.assertTrue(any("LastPaymentHistory" in url for url in gets))
        self.assertFalse(any("billdesk" in url.lower() or "unipay" in url.lower() for url in gets))

    def test_a_new_tab_is_read_and_a_payment_host_is_not(self):
        client = self._client()
        details = (FIXTURES / "result_bill_details.html").read_text(encoding="utf-8")
        launcher = (
            "<html><body><p>Please wait</p>"
            "<script>window.open('https://www.billdesk.com/pgidsk/PGI');</script>"
            "<script>window.open('/billpay/viewbill.aspx');</script>"
            "</body></html>"
        )
        gets = []

        def get(url, timeout=None, headers=None):
            gets.append(url)
            if "Img.aspx" in url or "capt.aspx" in url:
                return _Response(200, content=self.image)
            if "viewbill.aspx" in url:
                return _Response(200, text=details)
            return _Response(200, text=self.form)

        def post(url, data, timeout=None, allow_redirects=True, headers=None):
            return _Response(200, text=launcher)

        client.session.get = get
        client.session.post = post
        result = client.fetch("60099990002")
        self.assertEqual(result.status, "Paid")
        self.assertEqual(result.verified_ca, "60099990002")
        self.assertTrue(any("viewbill.aspx" in url for url in gets))
        self.assertFalse(any("billdesk" in url.lower() for url in gets))

    def test_layout_change_saves_html_and_a_snapshot(self):
        import tempfile

        client = self._client()
        broken = """
        <html><body>
        <h2>BILL DETAILS</h2>
        <table>
          <tr><td>CA NO.</td><td>60099990002</td></tr>
          <tr><td>Name :</td><td>Ms. ASHA .</td></tr>
        </table>
        <p>Net Banking</p><img alt="BillDesk" />
        <input value="0" />
        </body></html>
        """

        def get(url, timeout=None, headers=None):
            if "Img.aspx" in url or "capt.aspx" in url:
                return _Response(200, content=self.image)
            return _Response(200, text=self.form)

        def post(url, data, timeout=None, allow_redirects=True, headers=None):
            return _Response(200, text=broken)

        with tempfile.TemporaryDirectory() as folder:
            client.debug_dir = Path(folder)
            client.session.get = get
            client.session.post = post
            result = client.fetch("60099990002")
            saved = list(Path(folder).iterdir())
        self.assertEqual(result.status, "Error")
        self.assertIsNone(result.amount)
        self.assertIn("Could not read", result.note)
        self.assertIn("Saved debug/", result.note)
        names = {path.name for path in saved}
        htmls = [name for name in names if name.endswith(".html")]
        pngs = [name for name in names if name.endswith(".png")]
        self.assertEqual(len(htmls), 1)
        self.assertEqual(len(pngs), 1)
        self.assertTrue(htmls[0].startswith("60099990002-"))


class RetrySelectionTests(unittest.TestCase):
    def test_only_captcha_errors_are_retried(self):
        self.assertTrue(captcha_retry_needed({
            "status": "Error",
            "note": "Captcha could not be solved after 6 tries. Captcha was rejected by the site",
        }))
        self.assertFalse(captcha_retry_needed({"status": "Unpaid", "note": ""}))
        self.assertFalse(captcha_retry_needed({"status": "Error", "note": "CA number was rejected"}))
        self.assertFalse(captcha_retry_needed({
            "status": "Error",
            "note": "Tesseract is not installed, so the captcha image cannot be read.",
        }))

    def test_main_retries_captcha_errors_once_at_the_end(self):
        from billcheck.ndpl import BillResult

        csv_path = ROOT / "data" / "tenants.csv"
        seen = []

        def fetch(ca_no):
            seen.append(ca_no)
            if ca_no.endswith("1") and seen.count(ca_no) == 1:
                return BillResult(ca_no, "Error", None, None, None, "Captcha could not be solved after 6 tries. Captcha image was not confident, so it was not submitted", "t")
            if ca_no.endswith("2"):
                return BillResult(ca_no, "Error", None, None, None, "CA number was rejected", "t")
            return BillResult(ca_no, "Paid", 0.0, None, None, "", "t")

        fake_client = mock.Mock()
        fake_client.fetch.side_effect = fetch
        rows = "CA.NO,NAME\n60000000001,A\n60000000002,B\n60000000003,C\n"
        path = ROOT / "data" / "_captcha_retry.csv"
        out = ROOT / "data" / "_captcha_retry_out.csv"
        path.write_text(rows, encoding="utf-8")
        try:
            with mock.patch.dict(os.environ, {"WEBAPP_URL": "", "RECEIPT_EMAIL": "owner@example.com", "NDPL_CAPTCHA_TRIES": "6"}, clear=False):
                with mock.patch("billcheck.check_bills.NdplClient", return_value=fake_client):
                    with mock.patch("billcheck.check_bills.time.sleep"):
                        code = main(["--csv", str(path), "--out", str(out), "--no-push", "--delay", "2"])
            self.assertEqual(code, 0)
            # Three first passes, then one extra pass for the captcha error only.
            self.assertEqual(seen, ["60000000001", "60000000002", "60000000003", "60000000001"])
            self.assertEqual(fake_client.fetch.call_count, 4)
        finally:
            path.unlink(missing_ok=True)
            out.unlink(missing_ok=True)

    def test_one_ca_failing_does_not_stop_the_run(self):
        from billcheck.ndpl import BillResult

        def fetch(ca_no):
            if ca_no.endswith("2"):
                raise RuntimeError("browser crashed")
            return BillResult(ca_no, "Paid", 0.0, "2026-10-16", "A", "", "t")

        fake_client = mock.Mock()
        fake_client.fetch.side_effect = fetch
        rows = "CA.NO,NAME\n60000000001,A\n60000000002,B\n60000000003,C\n"
        path = ROOT / "data" / "_one_ca_fail.csv"
        out = ROOT / "data" / "_one_ca_fail_out.csv"
        path.write_text(rows, encoding="utf-8")
        buffer = io.StringIO()
        try:
            with mock.patch.dict(os.environ, {"WEBAPP_URL": "", "RECEIPT_EMAIL": "owner@example.com"}, clear=False):
                with mock.patch("billcheck.check_bills.NdplClient", return_value=fake_client):
                    with mock.patch("billcheck.check_bills.time.sleep"):
                        with redirect_stdout(buffer):
                            code = main(["--csv", str(path), "--out", str(out), "--no-push", "--delay", "2"])
            self.assertEqual(code, 0)
            self.assertEqual(fake_client.fetch.call_count, 3)
            self.assertIn("Run summary: ok=2 paid=2 unpaid=0 errors=1", buffer.getvalue())
            with out.open(newline="", encoding="utf-8") as handle:
                written = list(csv.DictReader(handle))
            by_ca = {row["ca_no"]: row for row in written}
            self.assertEqual(by_ca["60000000002"]["status"], "Error")
            self.assertIn("browser crashed", by_ca["60000000002"]["note"])
            self.assertEqual(by_ca["60000000001"]["status"], "Paid")
            self.assertEqual(by_ca["60000000003"]["status"], "Paid")
        finally:
            path.unlink(missing_ok=True)
            out.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
