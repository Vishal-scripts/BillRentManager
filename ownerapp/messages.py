"""Plain-language notes for a failed bill check."""

from __future__ import annotations


def explain_failure(note: str) -> dict:
    text = str(note or "").strip()
    low = text.lower()
    if "email" in low or "mobile" in low or "e-receipt" in low:
        step = "Before opening the site"
        plain = "The email or mobile number is missing, so the bill check did not start. Nothing was paid."
        fix = "Save the email and the 10-digit mobile, then press Retry this."
    elif "tesseract" in low:
        step = "Solving captcha"
        plain = "Tesseract is not available, so the captcha could not be read."
        fix = "Open the fix page, install Tesseract or use the portable folder, restart the app, then press Retry this."
    elif "captcha" in low:
        step = "Solving captcha"
        plain = "The captcha was not clear, so the number was not sent."
        fix = "Press Retry this. Nothing is paid."
    elif "error page" in low or "technical issue" in low or "stderr.aspx" in low:
        step = "Opening the site"
        plain = "Tata Power returned an error page instead of the bill form."
        fix = "Wait a few minutes, then press Retry this. The app will try again with a fresh session."
    elif "406" in low or "blocked" in low or "suspicious" in low:
        step = "Opening the site"
        plain = "Tata Power blocked this request."
        fix = "Wait a few minutes, then press Retry this. Do not press All again right away."
    elif "timeout" in low or "network" in low or "connection" in low:
        step = "Opening the site"
        plain = "The site did not open."
        fix = "Check the internet connection, then press Retry this."
    elif "could not read" in low or "not found" in low or "rejected" in low or "match" in low:
        step = "Reading the bill"
        plain = "The bill page could not be read."
        fix = "Press Retry this. If this keeps happening, the page wording may have changed."
    else:
        step = "Bill check"
        plain = text or "The bill check failed."
        fix = "Press Retry this. Nothing is paid."
    return {"step": step, "plain": plain, "fix": fix}
