"""The Tata Power pay-bill form fields this app is allowed to fill.

The archived form (paybillonline.aspx) marks email as required
("* Email Id for E-receipt") and also asks for a mobile
("Mobile No. for E-receipt"). Both are sent with the CA number and the
captcha sum. The Pay Now button is the site's own first step. This app
never opens a card page.
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_mobile(value) -> str:
    if isinstance(value, float) and value == int(value):
        value = int(value)
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) == 12 and digits.startswith("91"):
        return digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        return digits[1:]
    return digits


def email_ok(value) -> bool:
    return bool(_EMAIL.match(str(value or "").strip()))


def mobile_ok(value) -> bool:
    return len(normalize_mobile(value)) == 10


def receipt_problems(email, mobile) -> list[str]:
    missing = []
    if not email_ok(email):
        missing.append("email")
    if not mobile_ok(mobile):
        missing.append("mobile")
    return missing


def receipt_ready(email, mobile) -> bool:
    return not receipt_problems(email, mobile)


def receipt_message(email, mobile) -> str:
    missing = receipt_problems(email, mobile)
    if not missing:
        return ""
    if missing == ["email"]:
        return (
            "Enter the email used for the bill check. Tata Power's form marks "
            "Email Id for E-receipt as required. Nothing is paid."
        )
    if missing == ["mobile"]:
        return (
            "Enter a 10-digit mobile. Tata Power's form asks for a mobile number "
            "for the e-receipt. Nothing is paid."
        )
    return (
        "Enter the email and 10-digit mobile Tata Power's form asks for. "
        "The email is marked required. The mobile is for the e-receipt. Nothing is paid."
    )
