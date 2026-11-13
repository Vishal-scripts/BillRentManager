"""Rent colours, the bill queue, and the local API. No network and no Google account."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ownerapp.app import create_app  # noqa: E402
from ownerapp.db import Database  # noqa: E402
from ownerapp.demo_bills import demo_fetch  # noqa: E402
from ownerapp.importer import read_path  # noqa: E402
from ownerapp.queue_logic import dedupe_jobs, lock_is_stale, merge_bill, select_cas  # noqa: E402
from billcheck.captcha import BillCheckStopped  # noqa: E402
from ownerapp.rent_logic import (  # noqa: E402
    months_to_close,
    parse_due_day,
    rent_tone,
)
from ownerapp.service import BillService  # noqa: E402
from billcheck.captcha import configure_tesseract  # noqa: E402

SAMPLE = ROOT / "data" / "sample_tenants.csv"
TODAY = date(2026, 10, 16)


def fast_fetch(ca_no, on_progress=None):
    return demo_fetch(ca_no, on_progress, pause=0)


class RentToneTests(unittest.TestCase):
    def test_due_day_labels(self):
        self.assertEqual(parse_due_day("18TH"), 18)
        self.assertEqual(parse_due_day("1ST"), 1)
        self.assertEqual(parse_due_day("5"), 5)
        self.assertIsNone(parse_due_day(""))
        self.assertIsNone(parse_due_day("32"))

    def test_colours_for_the_open_month(self):
        self.assertEqual(rent_tone(TODAY, 16, False).tone, "due")
        self.assertEqual(rent_tone(TODAY, 18, False).tone, "due")
        self.assertEqual(rent_tone(TODAY, 14, False).tone, "overdue")
        self.assertEqual(rent_tone(TODAY, 28, False).tone, "upcoming")
        self.assertEqual(rent_tone(TODAY, 14, True).tone, "paid")
        self.assertEqual(rent_tone(TODAY, 31, False, "2026-02").due, date(2026, 2, 28))

    def test_past_month_is_paid_or_unpaid_only(self):
        self.assertEqual(rent_tone(TODAY, 20, False, "2026-09").tone, "overdue")
        self.assertEqual(rent_tone(TODAY, 20, True, "2026-09").tone, "paid")

    def test_first_launch_does_not_invent_history(self):
        self.assertEqual(months_to_close(TODAY, None), [])
        self.assertEqual(months_to_close(TODAY, "2026-09"), [])
        self.assertEqual(months_to_close(date(2026, 11, 2), "2026-09"), ["2026-10"])
        self.assertEqual(months_to_close(date(2026, 11, 2), "2026-08"), ["2026-09", "2026-10"])

    def test_rollover_keeps_a_paid_row_and_fills_the_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "rent.db")
            tenant = db.save_tenant({"name": "Asha", "rent": 1000, "due_day": 18, "active": True})
            db.mark_rent(tenant["id"], "2026-10", "paid", 1000, "2026-10-18", "UPI", "")
            db.set_setting("closed_through", "2026-08")
            closed = db.rollover(date(2026, 11, 2))
            self.assertEqual(closed, ["2026-09", "2026-10"])
            rows = db.rent_rows("2026-10", date(2026, 11, 2))
            self.assertTrue(rows[0]["paid"])
            september = db.rent_rows("2026-09", date(2026, 11, 2))
            self.assertEqual(september[0]["tone"], "overdue")
            again = db.rollover(date(2026, 11, 2))
            self.assertEqual(again, [])
            db.close()


class QueueRuleTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"ca_no": "60000000001", "name": "A", "status": "Unpaid", "check_state": "Done", "active": True},
            {"ca_no": "60000000002", "name": "B", "status": "Paid", "check_state": "Done", "active": True},
            {"ca_no": "60000000003", "name": "C", "status": "Error: Captcha", "check_state": "Error", "active": True},
        ]

    def test_each_refresh_mode(self):
        self.assertEqual(len(select_cas(self.rows, "all")["cas"]), 3)
        self.assertEqual(select_cas(self.rows, "unpaid")["cas"][0]["ca_no"], "60000000001")
        self.assertEqual(select_cas(self.rows, "error")["cas"][0]["ca_no"], "60000000003")
        self.assertEqual(select_cas(self.rows, "one", {"ca_no": "60000000002"})["cas"][0]["name"], "B")
        self.assertFalse(select_cas(self.rows, "one", {"ca_no": "123"})["ok"])
        selected = select_cas(self.rows, "selected", {"selected": ["60000000001", "60000000001", "60000000003"]})
        self.assertEqual([row["ca_no"] for row in selected["cas"]], ["60000000001", "60000000003"])

    def test_dedupe_stale_lock_and_error_keeps_the_old_amount(self):
        split = dedupe_jobs(["60000000001"], select_cas(self.rows, "all")["cas"])
        self.assertEqual([row["ca_no"] for row in split["add"]], ["60000000002", "60000000003"])
        now = 1_000_000
        self.assertFalse(lock_is_stale(now - 1000, now))
        self.assertTrue(lock_is_stale(now - 4 * 60 * 1000, now))
        merged = merge_bill(
            {"ca_no": "60000000003", "amount": 80, "due_date": "2026-10-01", "exact_bill": 80, "bill_no": "010"},
            {"ca_no": "60000000003", "status": "Error", "note": "Captcha", "checked_at": "t"},
        )
        self.assertEqual(merged["amount"], 80)
        self.assertEqual(merged["bill_no"], "010")
        self.assertEqual(merged["check_state"], "Error")

    def test_resume_puts_checking_back_on_the_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "q.db")
            db.enqueue_jobs([{"ca_no": "60000000001", "name": "A"}], "all")
            db.claim_queued("run")
            self.assertEqual(db.requeue_checking(), 1)
            self.assertEqual(db.open_job_cas(), ["60000000001"])
            service = BillService(db, fetch=fast_fetch, gap=0, today=lambda: TODAY)
            service.start()
            self.assertTrue(service.wait_until_idle())
            bill = db.bill_map()["60000000001"]
            self.assertEqual(bill["status"], "Unpaid")
            self.assertIn("Practice data", bill["note"])
            service.stop()
            db.close()

    def test_monday_style_auto_refresh_queues_once_that_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "auto.db")
            db.save_tenant({"name": "Asha", "ca_no": "60000000001", "rent": 100, "due_day": 16, "active": True})
            db.update_settings(
                {
                    "auto_refresh": True,
                    "auto_weekday": date.today().weekday(),
                    "auto_time": "00:00",
                }
            )
            service = BillService(db, fetch=fast_fetch, gap=0, today=lambda: TODAY)
            service._maybe_auto()
            service._maybe_auto()
            self.assertEqual(db.open_job_cas(), ["60000000001"])
            self.assertEqual(db.setting("last_auto_date"), date.today().isoformat())
            service.stop()
            db.close()


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = create_app(
            Path(self.tmp.name) / "app.db",
            pin="2468",
            demo=True,
            fetch=fast_fetch,
            today=lambda: TODAY,
            gap=0,
            users_path=Path(self.tmp.name) / "users.env",
        )
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.client.post("/api/login", json={"username": "admin", "pin": "2468"})

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_pin_wall(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.client.post("/api/logout")
        self.assertEqual(self.client.get("/api/tenants").status_code, 401)
        self.assertEqual(self.client.post("/api/login", json={"username": "admin", "pin": "0000"}).status_code, 401)
        self.assertEqual(self.client.post("/api/login", json={"username": "admin", "pin": "2468"}).status_code, 200)

    def test_dashboard_and_rent_mark(self):
        board = self.client.get("/api/dashboard").json()
        self.assertGreaterEqual(board["rent_due_today"]["count"], 1)
        self.assertGreater(board["collected"], 0)
        self.assertGreater(board["pending"], 0)
        tenant = next(row for row in self.client.get("/api/tenants").json()["tenants"] if row["name"] == "Asha Mehta")
        marked = self.client.post(
            "/api/rent/mark",
            json={"tenant_id": tenant["id"], "status": "paid", "amount": 12000, "paid_on": "2026-10-16", "mode": "Cash"},
        )
        self.assertEqual(marked.status_code, 200)
        rent = self.client.get("/api/rent").json()
        asha = next(row for row in rent["rows"] if row["name"] == "Asha Mehta")
        self.assertEqual(asha["tone"], "paid")

    def test_import_sample_and_export(self):
        rows = read_path(SAMPLE)
        self.assertEqual([row["ca_no"] for row in rows], ["60000000001", "60000000002", "60000000003"])
        self.assertNotIn("ASHA", SAMPLE.read_text(encoding="utf-8"))
        with SAMPLE.open("rb") as handle:
            imported = self.client.post("/api/tenants/import", files={"file": ("sample_tenants.csv", handle, "text/csv")})
        self.assertEqual(imported.status_code, 200)
        body = imported.json()
        self.assertGreaterEqual(body["rows"], 3)
        exported = self.client.get("/api/export.xlsx")
        self.assertEqual(exported.status_code, 200)
        self.assertTrue(exported.content.startswith(b"PK"))

    def test_every_refresh_option(self):
        def run(mode, **extra):
            queued = self.client.post("/api/bills/refresh", json={"mode": mode, **extra})
            self.assertEqual(queued.status_code, 200, queued.text)
            self.assertTrue(self.app.state.service.wait_until_idle())
            return queued.json(), self.app.state.service.snapshot()

        _, snap = run("all")
        self.assertIn("Run summary: ok=4 paid=3 unpaid=1 errors=1", snap["summary"])
        self.assertIn("captcha_rate=", snap["summary"])
        message, snap = run("unpaid")
        self.assertIn("Queued 1", message["message"])
        self.assertIn("Run summary: ok=1 paid=0 unpaid=1 errors=0", snap["summary"])
        self.assertIn("captcha_rate=", snap["summary"])
        _, snap = run("error")
        self.assertIn("Run summary: ok=0 paid=0 unpaid=0 errors=1", snap["summary"])
        _, snap = run("one", ca_no="60000000002")
        self.assertIn("Run summary: ok=1 paid=1 unpaid=0 errors=0", snap["summary"])
        _, snap = run("selected", selected=["60000000001", "60000000003"])
        self.assertIn("Run summary: ok=1 paid=0 unpaid=1 errors=1", snap["summary"])
        bad = self.client.post("/api/bills/refresh", json={"mode": "one", "ca_no": "123"})
        self.assertEqual(bad.status_code, 400)
        self.assertIn("11-digit", bad.json()["detail"])

    def test_a_bill_date_older_than_three_months_is_flagged(self):
        self.app.state.db.save_bill(
            {
                "ca_no": "60000000001",
                "status": "Paid",
                "amount": 0.0,
                "due_date": "2023-09-18",
                "exact_bill": 2318.0,
                "bill_no": "040007958604",
                "bill_date": "2023-09-18",
                "consumer_name": "Sample",
                "note": "",
                "checked_at": "2026-11-13T18:29:36+00:00",
                "check_state": "Done",
            }
        )
        self.app.state.db.save_bill(
            {
                "ca_no": "60000000002",
                "status": "Paid",
                "amount": 0.0,
                "due_date": "2026-10-16",
                "exact_bill": 50.0,
                "bill_no": "010000000002",
                "bill_date": "2026-10-01",
                "consumer_name": "Recent",
                "note": "",
                "checked_at": "2026-10-16T00:00:00+00:00",
                "check_state": "Done",
            }
        )
        rows = {row["ca_no"]: row for row in self.client.get("/api/bills").json()["bills"]}
        self.assertTrue(rows["60000000001"]["stale_bill"])
        self.assertIn("3 months", rows["60000000001"]["stale_note"])
        self.assertTrue(rows["60000000001"]["stale_note"].isascii())
        self.assertFalse(rows["60000000002"]["stale_bill"])
        self.assertEqual(rows["60000000002"]["stale_note"], "")
        tenant_id = rows["60000000001"]["tenant_id"]
        detail = self.client.get(f"/api/tenants/{tenant_id}/detail").json()
        self.assertTrue(detail["bill"]["stale_bill"])
        self.assertIn("3 months", detail["bill"]["stale_note"])

    def test_first_run_asks_for_a_pin_in_the_browser(self):
        fresh = tempfile.TemporaryDirectory()
        users = Path(fresh.name) / "users.env"
        app = create_app(
            Path(fresh.name) / "new.db",
            pin=None,
            fetch=fast_fetch,
            today=lambda: TODAY,
            gap=0,
            users_path=users,
        )
        with TestClient(app) as client:
            me = client.get("/api/me")
            self.assertEqual(me.status_code, 200)
            self.assertTrue(me.json()["needs_setup"])
            self.assertIn("8765", me.json()["phone_url"])
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            script = client.get("/static/app.js")
            self.assertIn('id="setup"', script.text)
            self.assertIn("Your name", script.text)
            bad = client.post("/api/setup", json={"pin": "12", "owner_name": "Vishal"})
            self.assertEqual(bad.status_code, 400)
            saved = client.post(
                "/api/setup",
                json={
                    "owner_name": "Vishal",
                    "pin": "1357",
                    "confirm": "1357",
                    "email": "owner@example.com",
                    "mobile": "9811111111",
                },
            )
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertTrue(saved.json()["ok"])
            self.assertFalse(saved.json()["needs_setup"])
            self.assertEqual(client.get("/api/tenants").status_code, 200)
            saved_users = users.read_text(encoding="utf-8")
            self.assertNotIn("1357", saved_users)
            self.assertIn("pbkdf2$", saved_users)
            self.assertTrue((users.parent / "pin.pepper").is_file())
            self.assertIn("Username", client.get("/static/app.js").text)
            again = client.post("/api/setup", json={"pin": "1357", "owner_name": "Vishal"})
            self.assertEqual(again.status_code, 400)
        fresh.cleanup()

    def test_pages_are_served(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertIn("manifest", self.client.get("/").text)
        self.assertEqual(self.client.get("/manifest.webmanifest").status_code, 200)
        self.assertEqual(self.client.get("/sw.js").status_code, 200)
        self.assertEqual(self.client.get("/static/app.css").status_code, 200)
        self.assertIn("Bill Rent Manager", self.client.get("/").text)
        manifest = self.client.get("/manifest.webmanifest").json()
        self.assertEqual(manifest["name"], "Bill Rent Manager")
        self.assertEqual(manifest["short_name"], "Bill Rent Manager")


class ContactAndImportTests(unittest.TestCase):
    def test_ist_clock_matches_the_owner_timestamp(self):
        from ownerapp.clock import format_ist

        self.assertEqual(format_ist("2026-10-16T08:27:35+00:00"), "16 Oct 2026, 1:57 PM")
        self.assertEqual(format_ist("2026-10-16", with_time=False), "16 Oct 2026")

    def test_plain_error_names_the_step(self):
        from ownerapp.messages import explain_failure

        captcha = explain_failure("Captcha could not be solved after 6 tries.")
        self.assertEqual(captcha["step"], "Solving captcha")
        self.assertIn("Retry", captcha["fix"])
        missing = explain_failure("tesseract not found")
        self.assertNotIn("nahi", missing["plain"].lower())
        self.assertIn("Tesseract", missing["plain"])

    def test_live_refresh_is_blocked_until_email_and_mobile_are_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(
                Path(tmp) / "live.db",
                pin="2468",
                demo=False,
                gap=0,
                users_path=Path(tmp) / "users.env",
            )
            with TestClient(app) as client:
                client.post("/api/login", json={"username": "admin", "pin": "2468"})
                app.state.db.save_tenant({"name": "Vicky", "ca_no": "60099990004", "active": True})
                app.state.service.stop()
                app.state.service._thread.join(timeout=2)
                blocked = client.post("/api/bills/refresh", json={"mode": "all"})
                self.assertEqual(blocked.status_code, 400)
                self.assertIn("email", blocked.json()["detail"].lower())
                self.assertEqual(app.state.db.open_job_cas(), [])
                saved = client.put(
                    "/api/settings",
                    json={"receipt_email": "owner@example.com", "receipt_mobile": "9811111111"},
                )
                self.assertEqual(saved.status_code, 200)
                self.assertTrue(saved.json()["receipt_ready"])
                queued = client.post("/api/bills/refresh", json={"mode": "one", "ca_no": "60099990004"})
                self.assertEqual(queued.status_code, 200, queued.text)
                self.assertEqual(app.state.db.open_job_cas(), ["60099990004"])

    def test_setup_requires_a_real_email_and_mobile(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(
                Path(tmp) / "fresh.db",
                pin=None,
                demo=False,
                gap=0,
                users_path=Path(tmp) / "users.env",
            )
            with TestClient(app) as client:
                missing = client.post(
                    "/api/setup",
                    json={"owner_name": "Vishal", "pin": "1357", "confirm": "1357", "email": "not-an-email", "mobile": "9811111111"},
                )
                self.assertEqual(missing.status_code, 400)
                short = client.post(
                    "/api/setup",
                    json={"owner_name": "Vishal", "pin": "1357", "confirm": "1357", "email": "owner@example.com", "mobile": "123"},
                )
                self.assertEqual(short.status_code, 400)

    def test_excel_round_trip_preview_and_duplicate_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(
                Path(tmp) / "xlsx.db",
                pin="2468",
                demo=True,
                fetch=fast_fetch,
                today=lambda: TODAY,
                gap=0,
                users_path=Path(tmp) / "users.env",
            )
            with TestClient(app) as client:
                client.post("/api/login", json={"username": "admin", "pin": "2468"})
                self._excel_round_trip(client)

    def _excel_round_trip(self, client):
        from io import BytesIO
        from openpyxl import load_workbook

        first = client.post(
            "/api/tenants/import",
            files={"file": ("sample.csv", SAMPLE.read_bytes(), "text/csv")},
        )
        self.assertEqual(first.status_code, 200, first.text)
        exported = client.get("/api/export.xlsx")
        self.assertEqual(exported.status_code, 200)
        book = load_workbook(BytesIO(exported.content))
        headers = [cell.value for cell in next(book["Tenants"].iter_rows(max_row=1))]
        self.assertIn("CA number", headers)
        self.assertGreater(book["Tenants"].column_dimensions["A"].width, 10)
        again = client.post(
            "/api/tenants/import?on_duplicate=skip",
            files={"file": ("again.xlsx", exported.content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["added"], 0)
        self.assertGreaterEqual(again.json()["skipped"], 3)
        bad = "NAME,CA.NO,PH. NO,RENT,DATE\nBad Person,12345,12,nope,99\n"
        preview = client.post(
            "/api/tenants/import?preview=1",
            files={"file": ("bad.csv", bad.encode(), "text/csv")},
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        body = preview.json()
        self.assertEqual(body["rejected"], 1)
        self.assertTrue(body["preview"])
        self.assertTrue(any("11 digits" in issue for issue in body["report"][0]["issues"]))
        names = [row["name"] for row in client.get("/api/tenants").json()["tenants"]]
        self.assertNotIn("Bad Person", names)
        template = client.get("/api/tenants/template.xlsx")
        self.assertEqual(template.status_code, 200)
        self.assertTrue(template.content.startswith(b"PK"))

    def test_cancel_leaves_the_remaining_ca_unchecked(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "stop.db")
            db.save_tenant({"name": "Asha", "ca_no": "60000000001", "active": True})
            db.save_tenant({"name": "Bela", "ca_no": "60000000002", "active": True})
            seen = []

            def fetch(ca_no, on_progress=None):
                seen.append(ca_no)
                service.cancel()
                return fast_fetch(ca_no, on_progress)

            service = BillService(db, fetch=fetch, gap=0, today=lambda: TODAY)
            service.start()
            service.enqueue("all")
            deadline = time.time() + 5
            while time.time() < deadline and service.snapshot().get("state") not in ("Stopped", "Done"):
                time.sleep(0.02)
            service.stop()
            snap = service.snapshot()
            self.assertEqual(seen, ["60000000001"])
            self.assertEqual(snap["state"], "Stopped")
            self.assertTrue(any(item["step"] == "stopped" for item in snap["items"]))
            self.assertIn("Run summary", snap["summary"])
            db.close()

    def test_stop_leaves_the_current_ca_unchecked_within_a_few_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "stop-fast.db")
            db.save_tenant({"name": "Asha", "ca_no": "60000000001", "active": True})
            db.save_tenant({"name": "Bela", "ca_no": "60000000002", "active": True})
            db.save_bill(
                {
                    "ca_no": "60000000001",
                    "status": "Paid",
                    "amount": 10.0,
                    "due_date": "2026-10-16",
                    "exact_bill": 10.0,
                    "bill_no": "010000000001",
                    "bill_date": "2026-10-01",
                    "consumer_name": "Asha",
                    "note": "kept",
                    "checked_at": "2026-10-16T00:00:00+00:00",
                    "check_state": "Done",
                }
            )
            entered = threading.Event()

            def fetch(ca_no, on_progress=None, should_stop=None):
                entered.set()
                for attempt in range(1, 7):
                    if should_stop and should_stop():
                        raise BillCheckStopped()
                    if on_progress:
                        on_progress(
                            {
                                "ca_no": ca_no,
                                "captcha_try": attempt,
                                "captcha_tries": 6,
                                "phase": "captcha",
                            }
                        )
                    time.sleep(0.25)
                return fast_fetch(ca_no, on_progress)

            service = BillService(db, fetch=fetch, gap=0, today=lambda: TODAY)
            service.start()
            service.enqueue("all")
            self.assertTrue(entered.wait(3))
            started = time.perf_counter()
            service.cancel()
            deadline = time.time() + 3
            while time.time() < deadline and service.snapshot().get("state") == "Running":
                time.sleep(0.02)
            elapsed = time.perf_counter() - started
            service.stop()
            snap = service.snapshot()
            self.assertLess(elapsed, 2.0, snap.get("log"))
            self.assertEqual(snap["state"], "Stopped")
            self.assertTrue(snap["items"])
            self.assertTrue(all(item["step"] == "stopped" for item in snap["items"]))
            self.assertTrue(any("not checked" in (item.get("plain") or "") for item in snap["items"]))
            kept = db.bill_map()["60000000001"]
            self.assertEqual(kept["status"], "Paid")
            self.assertEqual(kept["amount"], 10.0)
            self.assertEqual(kept["note"], "kept")
            self.assertNotEqual(kept["check_state"], "Checking")
            db.close()

    def test_backup_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(
                Path(tmp) / "bak.db",
                pin="2468",
                demo=True,
                fetch=fast_fetch,
                today=lambda: TODAY,
                gap=0,
                users_path=Path(tmp) / "users.env",
            )
            with TestClient(app) as client:
                client.post("/api/login", json={"username": "admin", "pin": "2468"})
                blob = client.get("/api/backup.db")
                self.assertEqual(blob.status_code, 200)
                self.assertTrue(blob.content.startswith(b"SQLite format 3"))
                client.post("/api/tenants", json={"name": "Temp", "ca_no": "60000000099", "active": True})
                restored = client.post(
                    "/api/restore",
                    files={"file": ("backup.db", blob.content, "application/octet-stream")},
                )
                self.assertEqual(restored.status_code, 200, restored.text)
                names = [row["name"] for row in client.get("/api/tenants").json()["tenants"]]
                self.assertNotIn("Temp", names)


class UsersFileTests(unittest.TestCase):
    def test_roles_limit_and_skipped_lines(self):
        from ownerapp.users_file import parse_users

        lines = ["# comment", "admin=2468", "bad line", "x=12", "ramesh=4821", "priya=9090:admin"]
        lines.extend(f"user{i}=1111" for i in range(12))
        users, warnings = parse_users("\n".join(lines))
        self.assertEqual(users["admin"].role, "admin")
        self.assertEqual(users["ramesh"].role, "staff")
        self.assertEqual(users["priya"].role, "admin")
        self.assertEqual(len(users), 10)
        self.assertTrue(any("Skipped" in item for item in warnings))
        self.assertTrue(any("only 10" in item for item in warnings))

    def test_lockout_reload_and_staff_limits(self):
        from ownerapp.doctor import probe_tesseract

        missing = probe_tesseract("/no/such/tesseract.exe")
        self.assertFalse(missing["ok"])
        self.assertIn("Tesseract", missing["fix"])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.env"
            path.write_text("admin=2468\nramesh=4821\n", encoding="utf-8")
            app = create_app(
                Path(tmp) / "users.db",
                pin=None,
                demo=True,
                fetch=fast_fetch,
                today=lambda: TODAY,
                gap=0,
                users_path=path,
            )
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/users").status_code, 404)
                for _ in range(4):
                    self.assertEqual(
                        client.post("/api/login", json={"username": "ramesh", "pin": "0000"}).status_code,
                        401,
                    )
                self.assertEqual(
                    client.post("/api/login", json={"username": "ramesh", "pin": "0000"}).status_code,
                    429,
                )
                self.assertEqual(
                    client.post("/api/login", json={"username": "ramesh", "pin": "4821"}).status_code,
                    429,
                )
                app.state.users.fails["ramesh"] = (5, time.time() - 5)
                staff = client.post("/api/login", json={"username": "ramesh", "pin": "4821"})
                self.assertEqual(staff.status_code, 200, staff.text)
                self.assertEqual(staff.json()["role"], "staff")
                self.assertEqual(client.delete("/api/tenants/1").status_code, 403)
                self.assertEqual(client.put("/api/settings", json={"receipt_email": "a@b.co"}).status_code, 403)
                self.assertEqual(client.get("/api/backup.db").status_code, 403)
                self.assertEqual(client.get("/api/activity").status_code, 403)
                added = client.post("/api/tenants", json={"name": "New", "rent": 10, "active": True})
                self.assertEqual(added.status_code, 200, added.text)
                marked = client.post(
                    "/api/rent/mark",
                    json={"tenant_id": added.json()["id"], "status": "paid", "amount": 10, "paid_on": "2026-10-16"},
                )
                self.assertEqual(marked.status_code, 200, marked.text)
                path.write_text("admin=2468\n", encoding="utf-8")
                self.assertEqual(client.get("/api/tenants").status_code, 401)
                admin = client.post("/api/login", json={"username": "admin", "pin": "2468"})
                self.assertEqual(admin.status_code, 200, admin.text)
                activity = client.get("/api/activity").json()["activity"]
                self.assertTrue(any(row["username"] == "ramesh" and row["action"] == "mark rent" for row in activity))
                report = client.get("/api/ready")
                self.assertEqual(report.status_code, 200)
                self.assertIn("checks", report.json())


class BundledTesseractTests(unittest.TestCase):
    def test_relative_tesseract_exe_is_chosen(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "tesseract" / "tesseract.exe"
            exe.parent.mkdir()
            exe.write_bytes(b"")
            (exe.parent / "tessdata").mkdir()
            old_cmd = os.environ.pop("TESSERACT_CMD", None)
            old_home = os.environ.get("NARELA_HOME")
            old_prefix = os.environ.get("TESSDATA_PREFIX")
            os.environ["NARELA_HOME"] = tmp
            os.environ.pop("TESSDATA_PREFIX", None)
            try:
                chosen = configure_tesseract()
                self.assertEqual(Path(chosen), exe)
                self.assertEqual(
                    os.environ["TESSDATA_PREFIX"],
                    str(exe.parent / "tessdata") + os.sep,
                )
            finally:
                if old_cmd is None:
                    os.environ.pop("TESSERACT_CMD", None)
                else:
                    os.environ["TESSERACT_CMD"] = old_cmd
                if old_home is None:
                    os.environ.pop("NARELA_HOME", None)
                else:
                    os.environ["NARELA_HOME"] = old_home
                if old_prefix is None:
                    os.environ.pop("TESSDATA_PREFIX", None)
                else:
                    os.environ["TESSDATA_PREFIX"] = old_prefix
                configure_tesseract()

    def _restore_tesseract_env(self, saved: dict) -> None:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        configure_tesseract()

    def test_per_user_localappdata_tesseract_is_chosen(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "Programs" / "Tesseract-OCR" / "tesseract.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"")
            saved = {
                "TESSERACT_CMD": os.environ.get("TESSERACT_CMD"),
                "NARELA_HOME": os.environ.get("NARELA_HOME"),
                "LOCALAPPDATA": os.environ.get("LOCALAPPDATA"),
                "TESSDATA_PREFIX": os.environ.get("TESSDATA_PREFIX"),
            }
            os.environ.pop("TESSERACT_CMD", None)
            os.environ.pop("NARELA_HOME", None)
            os.environ.pop("TESSDATA_PREFIX", None)
            os.environ["LOCALAPPDATA"] = tmp
            try:
                chosen = configure_tesseract()
                self.assertEqual(Path(chosen), exe)
            finally:
                self._restore_tesseract_env(saved)

    def test_userprofile_fallback_finds_the_per_user_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "AppData" / "Local" / "Programs" / "Tesseract-OCR" / "tesseract.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"")
            saved = {
                "TESSERACT_CMD": os.environ.get("TESSERACT_CMD"),
                "NARELA_HOME": os.environ.get("NARELA_HOME"),
                "LOCALAPPDATA": os.environ.get("LOCALAPPDATA"),
                "USERPROFILE": os.environ.get("USERPROFILE"),
                "TESSDATA_PREFIX": os.environ.get("TESSDATA_PREFIX"),
            }
            os.environ.pop("TESSERACT_CMD", None)
            os.environ.pop("NARELA_HOME", None)
            os.environ.pop("LOCALAPPDATA", None)
            os.environ.pop("TESSDATA_PREFIX", None)
            os.environ["USERPROFILE"] = tmp
            try:
                chosen = configure_tesseract()
                self.assertEqual(Path(chosen), exe)
            finally:
                self._restore_tesseract_env(saved)

    def test_portable_copy_still_wins_over_localappdata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundled = root / "app" / "tesseract" / "tesseract.exe"
            per_user = root / "local" / "Programs" / "Tesseract-OCR" / "tesseract.exe"
            bundled.parent.mkdir(parents=True)
            per_user.parent.mkdir(parents=True)
            bundled.write_bytes(b"")
            per_user.write_bytes(b"")
            saved = {
                "TESSERACT_CMD": os.environ.get("TESSERACT_CMD"),
                "NARELA_HOME": os.environ.get("NARELA_HOME"),
                "LOCALAPPDATA": os.environ.get("LOCALAPPDATA"),
                "TESSDATA_PREFIX": os.environ.get("TESSDATA_PREFIX"),
            }
            os.environ.pop("TESSERACT_CMD", None)
            os.environ.pop("TESSDATA_PREFIX", None)
            os.environ["NARELA_HOME"] = str(root / "app")
            os.environ["LOCALAPPDATA"] = str(root / "local")
            try:
                chosen = configure_tesseract()
                self.assertEqual(Path(chosen), bundled)
            finally:
                self._restore_tesseract_env(saved)

    def test_windows_launchers_check_the_per_user_folder(self):
        needle = r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"
        start = (ROOT / "ownerapp" / "Start.bat").read_text(encoding="utf-8")
        setup = (ROOT / "ownerapp" / "Setup.bat").read_text(encoding="utf-8")
        self.assertGreaterEqual(start.count(needle), 2)
        self.assertIn(needle, setup)
        live = (ROOT / "scripts" / "live_bill_check.py").read_text(encoding="utf-8")
        self.assertIn(needle, live)


class AlertTextTests(unittest.TestCase):
    def test_missing_tesseract_note_names_bill_rent_manager(self):
        from billcheck.ndpl import _TESSERACT_NOTE

        self.assertIn("tesseract\\tesseract.exe next to BillRentManager.exe", _TESSERACT_NOTE)
        self.assertNotIn("NarelaRentApp", _TESSERACT_NOTE)
        task = (ROOT / "ownerapp" / "Install-Login-Task.bat").read_text(encoding="utf-8")
        self.assertIn('set "TASK=BillRentManager"', task)
        self.assertIn('schtasks /Delete /TN "NarelaRentApp"', task)

    def test_rent_due_soon_alert_uses_an_ascii_hyphen(self):
        from ownerapp.alerts import sync_alerts
        from ownerapp.notifications import build_notifications

        today = date(2026, 11, 13)
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "alerts.db")
            db.save_tenant(
                {
                    "name": "SATYA PARKASH",
                    "rent": 1000,
                    "due_day": 15,
                    "ca_no": "60099990011",
                    "active": True,
                }
            )
            db.save_tenant(
                {
                    "name": "Asha",
                    "rent": 800,
                    "due_day": 10,
                    "ca_no": "60099990012",
                    "active": True,
                }
            )
            self.assertEqual(sync_alerts(db, today), 2)
            bodies = [row["body"] for row in db.list_alerts()]
            self.assertIn("SATYA PARKASH - due soon (2026-11-15).", bodies)
            self.assertTrue(all("\ufffd" not in body and "\u2014" not in body for body in bodies))
            notes = {item["name"]: item["body"] for item in build_notifications(db, today)}
            self.assertEqual(notes["SATYA PARKASH"], "SATYA PARKASH - due in 2 day(s).")
            self.assertEqual(notes["Asha"], "Asha - unpaid for this month.")
            self.assertTrue(all("\ufffd" not in body and "\u2014" not in body for body in notes.values()))
            db.close()

    def test_stored_replacement_character_and_old_exe_name_are_cleaned(self):
        import sqlite3

        from ownerapp.alerts import clean_alert_text

        self.assertEqual(
            clean_alert_text("SATYA PARKASH \ufffd due soon (2026-11-15)."),
            "SATYA PARKASH - due soon (2026-11-15).",
        )
        self.assertEqual(
            clean_alert_text("next to NarelaRentApp.exe"),
            "next to BillRentManager.exe",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "owner.db"
            db = Database(path)
            db.close()
            raw = sqlite3.connect(path)
            raw.execute(
                "INSERT INTO alerts(kind, title, body, created_at, dedupe_key) VALUES (?, ?, ?, ?, ?)",
                ("rent", "Rent due", "SATYA PARKASH \ufffd due soon (2026-11-15).", "t", "legacy-ufffd"),
            )
            raw.execute(
                "INSERT INTO alerts(kind, title, body, created_at, dedupe_key) VALUES (?, ?, ?, ?, ?)",
                (
                    "bill",
                    "Bill check error",
                    "The portable app looks for tesseract\\tesseract.exe next to NarelaRentApp.exe.",
                    "t",
                    "legacy-exe",
                ),
            )
            dash = "SATYA PARKASH \x97 due soon (2026-11-15).".encode("latin-1")
            raw.execute(
                """
                INSERT INTO alerts(kind, title, body, created_at, dedupe_key)
                VALUES ('rent', 'Rent due', CAST(? AS TEXT), 't', 'legacy-cp1252')
                """,
                (dash,),
            )
            raw.commit()
            raw.close()
            reopened = Database(path)
            by_key = {row["dedupe_key"]: row["body"] for row in reopened.list_alerts()}
            self.assertEqual(by_key["legacy-ufffd"], "SATYA PARKASH - due soon (2026-11-15).")
            self.assertEqual(
                by_key["legacy-exe"],
                "The portable app looks for tesseract\\tesseract.exe next to BillRentManager.exe.",
            )
            self.assertEqual(by_key["legacy-cp1252"], "SATYA PARKASH - due soon (2026-11-15).")
            self.assertTrue(reopened.add_alert("rent", "Rent due", "A \u2014 due soon (2026-11-15).", "fresh"))
            fresh = next(row["body"] for row in reopened.list_alerts() if row["dedupe_key"] == "fresh")
            self.assertEqual(fresh, "A - due soon (2026-11-15).")
            self.assertNotIn("\ufffd", fresh)
            reopened.close()


class AuditFixTests(unittest.TestCase):
    def test_listen_host_stays_local_until_phone_access(self):
        from ownerapp.paths import listen_host

        self.assertEqual(listen_host("", False), "127.0.0.1")
        self.assertEqual(listen_host("", True), "0.0.0.0")
        self.assertEqual(listen_host("0.0.0.0", False), "0.0.0.0")

    def test_easy_pin_is_refused_and_the_hash_is_not_the_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(Path(tmp) / "easy.db", pin=None, fetch=fast_fetch, today=lambda: TODAY, gap=0, users_path=Path(tmp) / "users.env")
            with TestClient(app) as client:
                refused = client.post(
                    "/api/setup",
                    json={"owner_name": "Vishal", "pin": "0000", "confirm": "0000", "email": "owner@example.com", "mobile": "9811111111"},
                )
                self.assertEqual(refused.status_code, 400)
                self.assertIn("too easy", refused.json()["detail"].lower())

    def test_setup_backup_and_restore_are_local_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            users = Path(tmp) / "users.env"
            app = create_app(Path(tmp) / "lan.db", pin="2468", fetch=fast_fetch, today=lambda: TODAY, gap=0, users_path=users)
            import asyncio
            import httpx

            async def remote_calls():
                transport = httpx.ASGITransport(app=app, client=("192.168.1.20", 50000))
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as remote:
                    signed = await remote.post("/api/login", json={"username": "admin", "pin": "2468"})
                    tenants = await remote.get("/api/tenants")
                    backup = await remote.get("/api/backup.db")
                    phone = await remote.post("/api/phone-access", json={"enabled": True})
                    return signed, tenants, backup, phone

            signed, tenants, backup, phone = asyncio.run(remote_calls())
            self.assertEqual(signed.status_code, 200, signed.text)
            self.assertEqual(tenants.status_code, 200)
            self.assertEqual(backup.status_code, 403)
            self.assertEqual(phone.status_code, 403)
            with TestClient(app) as local:
                local.post("/api/login", json={"username": "admin", "pin": "2468"})
                turned = local.post("/api/phone-access", json={"enabled": True})
                self.assertEqual(turned.status_code, 200, turned.text)
                self.assertTrue(turned.json()["enabled"])
                self.assertTrue((users.parent / "phone-access.on").is_file())
                backup = local.get("/api/backup.db")
                self.assertEqual(backup.status_code, 200)
                import sqlite3

                probe = sqlite3.connect(":memory:")
                # The backup is a file image. Open it from a temp path.
                blob = Path(tmp) / "backup.db"
                blob.write_bytes(backup.content)
                probe = sqlite3.connect(blob)
                keys = [row[0] for row in probe.execute("SELECT key FROM settings")]
                probe.close()
                self.assertNotIn("session_secret", keys)
                settings = local.get("/api/settings").json()
                self.assertTrue(settings["has_pin"])
                self.assertEqual(settings["captcha_tries"], "6")

    def test_lockout_survives_a_new_process(self):
        from ownerapp.users_file import UserDirectory

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "users.env"
            path.write_text("admin=2468\n", encoding="utf-8")
            first = UserDirectory(path)
            for _ in range(5):
                user, status = first.authenticate("admin", "9999")
            self.assertEqual(status, "locked")
            second = UserDirectory(path)
            user, status = second.authenticate("admin", "2468")
            self.assertEqual(status, "locked")
            self.assertIsNone(user)

    def test_phone_amount_past_month_delay_and_heartbeat(self):
        from ownerapp.demo_bills import seed_demo
        from ownerapp.rent_logic import month_key

        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "rules.db")
            short = None
            try:
                db.save_tenant({"name": "Short", "phone": "12345", "active": True})
            except ValueError as exc:
                short = str(exc)
            self.assertEqual(short, "Phone must be 10 digits.")
            tenant = db.save_tenant({"name": "New", "phone": "9811111111", "rent": 500, "due_day": 9, "active": True})
            with self.assertRaises(ValueError):
                db.mark_rent(tenant["id"], "2026-11", "paid", -5, "2026-11-09", "Cash", "")
            with self.assertRaises(ValueError):
                db.mark_rent(tenant["id"], "2026-11", "paid", 0, "2026-11-09", "Cash", "")
            october = db.rent_rows("2026-10", date(2026, 11, 9))
            self.assertEqual(october[0]["tone_label"], "Not a tenant yet")
            saved = db.update_settings({"captcha_tries": 12, "request_delay": 1, "receipt_email": "owner@example.com", "receipt_mobile": "9811111111"})
            self.assertEqual(saved["captcha_tries"], "12")
            db.save_tenant({"name": "New", "phone": "9811111111", "rent": 500, "due_day": 9, "ca_no": "60000000021", "active": True}, tenant["id"])
            service = BillService(db, fetch=None, gap=None, today=lambda: date(2026, 11, 9))
            self.assertEqual(service._pause(), 2.0)
            db.set_setting("request_delay", "9")
            self.assertEqual(service._pause(), 9.0)
            db.set_setting("auto_refresh", "true")
            db.set_setting("auto_weekday", str(date.today().weekday()))
            db.set_setting("auto_time", "00:00")
            db.set_setting("receipt_email", "")
            service._maybe_auto()
            self.assertIsNone(db.setting("last_auto_date"))
            self.assertEqual(db.open_job_cas(), [])
            db.set_setting("receipt_email", "owner@example.com")
            from unittest import mock
            with mock.patch("ownerapp.service.run_checks", return_value={"ok": False, "headline": "Tesseract was not found."}):
                service._maybe_auto()
            self.assertIsNone(db.setting("last_auto_date"))
            self.assertEqual(db.open_job_cas(), [])
            db.enqueue_jobs([{"ca_no": "60000000001", "name": "A"}], "all")
            claimed = db.claim_queued("run")
            db.touch_job(claimed[0]["id"], now_ms=1_000)
            self.assertEqual(db.requeue_stale_checking(now_ms=1_000 + 4 * 60 * 1000), 1)
            self.assertEqual(db.open_job_cas(), ["60000000001"])
            for day in (date(2026, 11, 1), date(2026, 10, 31), date(2026, 10, 16)):
                folder = Path(tmp) / day.isoformat()
                folder.mkdir()
                demo = Database(folder / "demo.db")
                seed_demo(demo, day)
                tones = {row["ca_no"]: row["tone_label"] for row in demo.rent_rows(month_key(day), day)}
                self.assertEqual(tones["60000000001"], "Due today", day)
                self.assertEqual(tones["60000000002"], "Due soon", day)
                self.assertEqual(tones["60000000003"], "Overdue", day)
                demo.close()
            db.close()

    def test_excel_formula_is_text_on_export_and_rejected_on_import(self):
        from io import BytesIO
        from openpyxl import Workbook, load_workbook

        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(
                Path(tmp) / "xl.db",
                pin="2468",
                demo=True,
                fetch=fast_fetch,
                today=lambda: TODAY,
                gap=0,
                users_path=Path(tmp) / "users.env",
            )
            with TestClient(app) as client:
                client.post("/api/login", json={"username": "admin", "pin": "2468"})
                made = client.post("/api/tenants", json={"name": "=2+2", "phone": "9811111111", "rent": 100, "due_day": 16, "active": True})
                self.assertEqual(made.status_code, 200, made.text)
                exported = client.get("/api/export.xlsx")
                book = load_workbook(BytesIO(exported.content))
                found = None
                for row in book["Tenants"].iter_rows(min_row=2, max_col=1):
                    if row[0].value == "=2+2":
                        found = row[0]
                self.assertIsNotNone(found)
                self.assertEqual(found.data_type, "s")
                formula = Workbook()
                sheet = formula.active
                sheet.title = "Tenants"
                sheet.append(["Name", "Phone", "CA number"])
                sheet["A2"] = '=1+1'
                sheet["B2"] = "9811111111"
                sheet["C2"] = "60000000088"
                payload = BytesIO()
                formula.save(payload)
                imported = client.post(
                    "/api/tenants/import",
                    files={"file": ("bad.xlsx", payload.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                )
                self.assertEqual(imported.status_code, 200, imported.text)
                self.assertEqual(imported.json()["rejected"], 1)
                self.assertTrue(any("formula" in issue.lower() for issue in imported.json()["report"][0]["issues"]))
                names = [row["name"] for row in client.get("/api/tenants").json()["tenants"]]
                self.assertNotIn("2", names)


if __name__ == "__main__":
    unittest.main()
