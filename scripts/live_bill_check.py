#!/usr/bin/env python3
"""Live Tata Power bill check for a list of CA numbers (never pays)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault(
    "TESSERACT_CMD",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
)

from billcheck.ndpl import NdplClient  # noqa: E402


def main() -> int:
    email = os.environ.get("RECEIPT_EMAIL", "owner@example.com")
    mobile = os.environ.get("RECEIPT_MOBILE", "9811111111")
    cas = sys.argv[1:] or [
        "60099990001",
        "60099990005",
        "60099990006",
        "60099990007",
    ]
    client = NdplClient(
        email=email,
        mobile=mobile,
        captcha_tries=8,
        pause=time.sleep,
        debug_dir=ROOT / "data" / "debug",
    )
    print(f"{'CA':<14} {'status':<8} {'tries':<5} consumer / fields")
    print("-" * 72)
    for ca in cas:
        result = client.fetch(ca)
        tries = client.last_captcha_attempts
        print(
            f"{ca:<14} {result.status:<8} {tries:<5} "
            f"{result.consumer_name or ''} | bill {result.bill_no} "
            f"date {result.bill_date} due {result.due_date} "
            f"exact {result.exact_bill} out {result.amount} | {result.note or 'ok'}"
        )
        time.sleep(4)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
