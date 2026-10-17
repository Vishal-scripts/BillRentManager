#!/usr/bin/env python3
"""Check Tata Power-DDL bills for the Narela rent sheet.

Reads CA numbers from the Google Sheet web app, or from a CSV.
Writes data/bills_latest.csv and, when WEBAPP_URL is set, posts results
back so the sheet and the owner's WhatsApp summary update.

Never sends a payment and never opens a payment option. One CA failing
does not stop the run. Captcha failures are retried once at the end.
A JSON log is appended under billcheck/logs/.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from billcheck.captcha import captcha_tries_from_env, configure_tesseract  # noqa: E402
from billcheck.ndpl import BillResult, NdplClient  # noqa: E402
from billcheck.parsers import parse_bill_html, parse_form_fields  # noqa: E402

_SHEET_RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
_SHEET_REDIRECTS = {301, 302, 303, 307, 308}
_SHEET_TRANSIENT = (
    "timeout",
    "unavailable",
    "rate",
    "backend",
    "internal error",
    "try again",
    "temporar",
)


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def ca_from_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = []
        seen = set()
        for row in reader:
            raw = row.get("CA.NO") or row.get("ca_no") or row.get("CA") or ""
            digits = "".join(ch for ch in str(raw) if ch.isdigit())
            if len(digits) != 11 or digits in seen:
                continue
            seen.add(digits)
            rows.append(
                {
                    "sno": row.get("S.NO") or row.get("sno") or "",
                    "name": row.get("NAME") or row.get("name") or "",
                    "address": row.get("ADDRESS") or "",
                    "ca_no": digits,
                }
            )
        return rows


def _sheet_should_retry(body: dict) -> bool:
    text = str(body.get("error") or "").lower()
    return any(hint in text for hint in _SHEET_TRANSIENT)


def post_webapp(url: str, payload: dict, attempts: int = 4, pause=time.sleep) -> dict:
    """Apps Script web apps answer a POST with a redirect that drops the body.

    Post once without following, then post the same JSON to the redirect URL.
    Timeouts, network errors, and temporary sheet errors are retried with backoff.
    """
    last_error = "Sheet web app failed"
    tries = max(1, attempts)
    for attempt in range(tries):
        retry = False
        try:
            response = requests.post(url, json=payload, timeout=90, allow_redirects=False)
            if response.status_code in _SHEET_REDIRECTS:
                location = response.headers.get("Location")
                if not location:
                    return {"ok": False, "error": "Sheet web app redirected without a location"}
                response = requests.post(location, json=payload, timeout=90, allow_redirects=False)
            if response.status_code in _SHEET_RETRY_STATUSES:
                last_error = f"HTTP {response.status_code}"
                retry = True
            elif response.status_code >= 400:
                return {
                    "ok": False,
                    "error": f"HTTP {response.status_code}: {(response.text or '')[:300]}",
                }
            else:
                try:
                    body = response.json()
                except json.JSONDecodeError:
                    return {"ok": False, "error": (response.text or "")[:500]}
                if not isinstance(body, dict):
                    return {"ok": False, "error": "Sheet web app returned an unexpected payload"}
                if body.get("ok") is False and _sheet_should_retry(body):
                    last_error = str(body.get("error") or "Sheet web app error")
                    retry = True
                else:
                    return body
        except requests.RequestException as exc:
            last_error = str(exc)
            retry = True
        if not retry or attempt + 1 >= tries:
            break
        wait = min(8.0, 0.5 * (2 ** attempt))
        print(
            f"Sheet request failed ({last_error}). Attempt {attempt + 1} of {tries}.",
            file=sys.stderr,
        )
        pause(wait)
    return {"ok": False, "error": last_error}


def fetch_cas(url: str, token: str) -> list[dict]:
    body = post_webapp(url, {"token": token, "action": "list_cas"})
    if not body.get("ok"):
        raise RuntimeError(body.get("error") or "Sheet web app rejected list_cas")
    return body.get("cas") or []


def captcha_retry_needed(row: dict) -> bool:
    """True when this CA failed because the captcha could not be solved.

    Invalid CA numbers, missing amounts, and a missing Tesseract install are
    not retried. A second pass cannot fix those.
    """
    if row.get("status") != "Error":
        return False
    note = (row.get("note") or "").lower()
    if "captcha" not in note:
        return False
    if "tesseract is not installed" in note:
        return False
    return True


def summarize(results: list[dict]) -> dict[str, int]:
    paid = unpaid = errors = 0
    for row in results:
        status = row.get("status")
        if status == "Paid":
            paid += 1
        elif status == "Unpaid":
            unpaid += 1
        else:
            errors += 1
    return {"ok": paid + unpaid, "paid": paid, "unpaid": unpaid, "errors": errors}


def format_summary(summary: dict[str, int]) -> str:
    return (
        f"Run summary: ok={summary['ok']} paid={summary['paid']} "
        f"unpaid={summary['unpaid']} errors={summary['errors']}"
    )


CSV_FIELDS = [
    "ca_no",
    "amount",
    "due_date",
    "status",
    "consumer_name",
    "note",
    "checked_at",
    "bill_no",
    "bill_date",
    "exact_bill",
    "verified_ca",
    "last_payment",
    "last_paid_on",
]


def write_csv(path: Path, results: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in results:
            writer.writerow({key: row.get(key, "") for key in CSV_FIELDS})


class RunLog:
    """One JSON object per line, under billcheck/logs/."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, event: str, **fields) -> None:
        record = {
            "ts": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "event": event,
            **fields,
        }
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            print(f"Log write failed: {exc}", file=sys.stderr)

    def __call__(self, event: str, **fields) -> None:
        self.event(event, **fields)


def _failed(ca_no: str, note: str) -> BillResult:
    return BillResult(
        ca_no=ca_no,
        status="Error",
        amount=None,
        due_date=None,
        consumer_name=None,
        note=note,
        checked_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )


def run_demo() -> int:
    fixture_dir = ROOT / "fixtures"
    print("Demo parse (no network):")
    form = (fixture_dir / "form_paybillonline.html").read_text(encoding="utf-8")
    fields = parse_form_fields(form)
    print("  form fields:", ", ".join(sorted(fields)))
    for name in sorted(fixture_dir.glob("result_*.html")):
        parsed = parse_bill_html(name.read_text(encoding="utf-8"))
        print(
            f"  {name.name}: {parsed.status} amount={parsed.amount} due={parsed.due_date} "
            f"exact={parsed.exact_bill} bill_no={parsed.bill_no} name={parsed.consumer_name} "
            f"note={parsed.note}"
        )
    return 0


def probe(pay_url: str) -> int:
    print(f"Probing {pay_url}")
    try:
        response = requests.get(
            pay_url,
            timeout=25,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
    except requests.RequestException as exc:
        print(f"Network error: {exc}")
        return 2
    blocked = response.status_code == 406 or "apptrana" in response.text.lower()
    print(f"HTTP {response.status_code} bytes={len(response.content)} blocked={blocked}")
    if blocked:
        print("This network is blocked by Tata Power (AppTrana HTTP 406). Use the home PC.")
        return 2
    print("Form reachable:", "txtcano" in response.text.lower())
    return 0


def _check_one(client: NdplClient, ca_no: str, log: RunLog) -> BillResult:
    try:
        result = client.fetch(ca_no)
    except Exception as exc:
        result = _failed(ca_no, f"Bill check failed: {exc}")
        log.event("ca-failed", ca=ca_no, error=str(exc))
    log.event(
        "ca",
        ca=result.ca_no,
        status=result.status,
        amount=result.amount,
        due=result.due_date,
        bill_no=result.bill_no,
        exact_bill=result.exact_bill,
        note=result.note,
    )
    return result


def main(argv: list[str] | None = None) -> int:
    load_env(ROOT / ".env")
    load_env(REPO / ".env")
    parser = argparse.ArgumentParser(description="Check Tata Power-DDL bills for Narela rent.")
    parser.add_argument("--demo", action="store_true", help="Parse saved fixtures and exit")
    parser.add_argument("--probe", action="store_true", help="Fetch the pay-bill page once and report HTTP status")
    parser.add_argument("--csv", type=Path, help="Tenant CSV with a CA.NO column")
    parser.add_argument("--out", type=Path, default=REPO / "data" / "bills_latest.csv")
    parser.add_argument("--no-push", action="store_true", help="Do not post results to the sheet")
    parser.add_argument("--delay", type=float, default=float(os.environ.get("REQUEST_DELAY_SECONDS", "3")))
    args = parser.parse_args(argv)

    if args.demo:
        return run_demo()

    pay_url = os.environ.get("NDPL_PAY_URL", "https://www.tatapower-ddl.com/billpay/paybillonline.aspx")
    if args.probe:
        return probe(pay_url)

    delay = max(2.0, args.delay)
    webapp = os.environ.get("WEBAPP_URL", "").strip()
    token = os.environ.get("WEBAPP_TOKEN", "").strip()
    if webapp and not args.csv:
        if not token:
            print("WEBAPP_URL is set but WEBAPP_TOKEN is empty.", file=sys.stderr)
            return 2
        print("Reading CA numbers from the Google Sheet…")
        cas = fetch_cas(webapp, token)
    else:
        csv_path = args.csv or (REPO / "data" / "tenants.csv")
        if not csv_path.is_file():
            print(f"CSV not found: {csv_path}", file=sys.stderr)
            return 2
        print(f"Reading CA numbers from {csv_path}")
        cas = ca_from_csv(csv_path)

    if not cas:
        print("No 11-digit CA numbers found.")
        return 0

    log = RunLog(ROOT / "logs" / "check-bills.jsonl")
    print(f"Logging to {log.path}")
    tesseract_cmd = configure_tesseract()
    if tesseract_cmd is None and shutil.which("tesseract") is None:
        log.event("tesseract-missing")
        print(
            "Tesseract is not installed, so the captcha image cannot be read. "
            "On Windows: winget install UB-Mannheim.TesseractOCR. "
            "If tesseract.exe is not on PATH, set TESSERACT_CMD in billcheck/.env "
            r"to C:\Program Files\Tesseract-OCR\tesseract.exe",
            file=sys.stderr,
        )
        return 2
    tries = captcha_tries_from_env()
    print(
        f"{len(cas)} CA numbers. Waiting {delay:.0f}s between each. "
        f"Up to {tries} captcha tries per CA. This does not pay any bill."
    )

    mobile = os.environ.get("RECEIPT_MOBILE", "").strip()
    email = os.environ.get("RECEIPT_EMAIL", "").strip()
    if not email or "@" not in email:
        log.event("config-error", error="RECEIPT_EMAIL missing")
        print("Set RECEIPT_EMAIL in billcheck/.env (the form requires an email for the receipt).", file=sys.stderr)
        return 2

    client = NdplClient(
        pay_url=pay_url,
        mobile=mobile,
        email=email,
        captcha_tries=tries,
        debug_dir=ROOT / "debug",
        log=log,
    )
    results: list[dict] = []
    try:
        for index, row in enumerate(cas):
            if index:
                time.sleep(delay)
            print(f"[{index + 1}/{len(cas)}] {row['ca_no']} {row.get('name', '')}")
            result = _check_one(client, row["ca_no"], log)
            payload = result.as_dict()
            payload["name"] = row.get("name") or result.consumer_name
            results.append(payload)
            print(
                f"    {result.status} amount={result.amount} due={result.due_date} "
                f"exact={result.exact_bill} bill_no={result.bill_no} {result.note}"
            )
        retry_indexes = [index for index, row in enumerate(results) if captcha_retry_needed(row)]
        if retry_indexes:
            print(f"Retrying {len(retry_indexes)} CA numbers whose captcha could not be solved…")
            for index in retry_indexes:
                time.sleep(delay)
                row = cas[index]
                print(f"[retry] {row['ca_no']} {row.get('name', '')}")
                result = _check_one(client, row["ca_no"], log)
                payload = result.as_dict()
                payload["name"] = row.get("name") or result.consumer_name
                results[index] = payload
                print(
                    f"    {result.status} amount={result.amount} due={result.due_date} "
                    f"exact={result.exact_bill} bill_no={result.bill_no} {result.note}"
                )
    finally:
        if results:
            write_csv(args.out, results)
            print(f"Wrote {args.out}")
            summary = summarize(results)
            print(format_summary(summary))
            log.event("summary", **summary)

    if not results:
        return 0
    if args.no_push or not webapp:
        if not webapp:
            print("WEBAPP_URL is empty, so the sheet was not updated. Paste the CSV into the BillDrop tab.")
        return 0
    print("Sending results to the sheet…")
    body = post_webapp(
        webapp,
        {"token": token, "action": "upsert_bills", "notify": False, "bills": results},
    )
    log.event("sheet", ok=bool(body.get("ok")), error=body.get("error") or "")
    print(json.dumps(body, ensure_ascii=False))
    return 0 if body.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
