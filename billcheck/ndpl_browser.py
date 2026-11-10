"""Bill lookup through Microsoft Edge (Playwright). Same outcome as Fast mode; never pays."""

from __future__ import annotations

import time
from pathlib import Path

from pytesseract import TesseractNotFoundError

from billcheck.captcha import configure_tesseract, solve_captcha
from billcheck.ndpl import (
    BillResult,
    NdplClient,
    PAY_URL_DEFAULT,
    USER_AGENT,
    _TESSERACT_NOTE,
    _now,
)
from billcheck.parsers import SITE_ERROR_NOTE, parse_bill_html, site_error_html

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - optional dependency
    PlaywrightTimeout = Exception  # type: ignore[misc, assignment]
    sync_playwright = None  # type: ignore[assignment]


class NdplBrowserClient:
    def __init__(
        self,
        pay_url: str = PAY_URL_DEFAULT,
        mobile: str = "",
        email: str = "",
        timeout: int = 60,
        captcha_tries: int = 6,
        pause=time.sleep,
        debug_dir: Path | None = None,
        log=None,
    ) -> None:
        self.pay_url = pay_url
        self.mobile = mobile
        self.email = email
        self.timeout = timeout
        self.captcha_tries = max(1, captcha_tries)
        self._pause = pause
        self.debug_dir = Path(debug_dir) if debug_dir else None
        self.log = log
        self._helper = NdplClient(
            pay_url=pay_url,
            mobile=mobile,
            email=email,
            debug_dir=debug_dir,
            log=log,
        )

    def fetch(self, ca_no: str, on_progress=None) -> BillResult:
        if sync_playwright is None:
            return NdplClient._error(
                ca_no,
                "Playwright is not installed. Run: pip install playwright && playwright install msedge",
                _now(),
            )
        configure_tesseract()
        checked = _now()
        last_note = "Captcha image was not confident, so it was not submitted"
        for attempt in range(self.captcha_tries):
            self._emit(on_progress, ca_no, attempt + 1, "captcha")
            if attempt:
                self._pause(min(1.2, 0.35 * attempt))
            try:
                html, err = self._submit_once(ca_no)
            except TesseractNotFoundError:
                return NdplClient._error(ca_no, _TESSERACT_NOTE, checked)
            except Exception as exc:
                return NdplClient._error(ca_no, f"Browser bill check failed: {exc}", checked)
            if err:
                last_note = err
                continue
            parsed = parse_bill_html(html, expected_ca=ca_no)
            if parsed.captcha_error:
                last_note = "Captcha was rejected by the site"
                continue
            if site_error_html(html):
                return NdplClient._error(ca_no, SITE_ERROR_NOTE, checked)
            return self._helper._from_parse(ca_no, parsed, checked)
        return NdplClient._error(
            ca_no,
            f"Captcha could not be solved after {self.captcha_tries} tries. {last_note}",
            checked,
        )

    def _emit(self, on_progress, ca_no: str, captcha_try: int, phase: str) -> None:
        self._helper._emit(on_progress, ca_no, captcha_try, phase)

    def _submit_once(self, ca_no: str) -> tuple[str, str | None]:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            context = browser.new_context(user_agent=USER_AGENT)
            page = context.new_page()
            page.goto(self.pay_url, timeout=self.timeout * 1000)
            page.fill("#txtcano", ca_no)
            page.fill("#txtemail", self.email)
            page.fill("#txtmobile", self.mobile)
            captcha = page.locator("#Img").screenshot()
            total, _meta = solve_captcha(captcha)
            if total is None:
                browser.close()
                return "", "The captcha was not clear, so the number was not sent"
            page.fill("#TxtImgVer", str(total))
            page.click("#btnpay")
            page.wait_for_load_state("networkidle", timeout=self.timeout * 1000)
            html = page.content()
            browser.close()
            return html, None
