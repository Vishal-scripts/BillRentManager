"""Polite Tata Power-DDL bill lookup from the public pay-bill form.

One HTTP session is reused for every CA number. This does not bypass the
WAF, rotate proxies, or hide automation flags. If the site returns HTTP 406,
that CA is recorded as Error and later CAs in the same run are marked the
same way without calling the site again. A dropped connection rebuilds the
session and retries that CA once. One CA failing does not stop the run.

The script never pays a bill. It posts the CA number, the owner's email, and
the captcha sum because that is the site's own first step. It does not send
card details, it does not open Net Banking, SBI Unipay, or BillDesk, and it
does not follow a payment button. A same-origin bill page opened in a new
tab is read with GET only.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from pytesseract import TesseractNotFoundError

from billcheck.captcha import captcha_pause, save_captcha_attempt, solve_captcha
from billcheck.parsers import (
    SITE_ERROR_NOTE,
    bill_parse_verified,
    parse_bill_html,
    parse_form_fields,
    parse_payment_history,
    site_error_html,
    visible_text,
    BillParse,
)

PAY_URL_DEFAULT = "https://www.tatapower-ddl.com/billpay/paybillonline.aspx"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_CAPTCHA_TRIES = 6
_SITE_ERROR_ATTEMPTS = 3
_SITE_ERROR_BACKOFF = (5, 15, 30)
_GATEWAY_MARKERS = ("billdesk", "payu", "razorpay", "ccavenue", "unipay", "sbiepay")
_RETRY_NETWORK = (requests.Timeout, requests.ConnectionError)


class WafBlocked(RuntimeError):
    """Tata Power's firewall rejected the request before the form loaded."""


class SiteDown(RuntimeError):
    """The pay-bill site returned a maintenance or unavailable page."""


class SiteErrorPage(RuntimeError):
    """Tata Power returned stderr.aspx or the technical-issue page after POST."""


@dataclass
class BillResult:
    ca_no: str
    status: str
    amount: float | None
    due_date: str | None
    consumer_name: str | None
    note: str
    checked_at: str
    bill_no: str | None = None
    bill_date: str | None = None
    exact_bill: float | None = None
    verified_ca: str | None = None
    last_payment: float | None = None
    last_paid_on: str | None = None

    def as_dict(self) -> dict:
        return {
            "ca_no": self.ca_no,
            "status": self.status,
            "amount": self.amount,
            "due_date": self.due_date or "",
            "consumer_name": self.consumer_name or "",
            "note": self.note,
            "checked_at": self.checked_at,
            "bill_no": self.bill_no or "",
            "bill_date": self.bill_date or "",
            "exact_bill": self.exact_bill if self.exact_bill is not None else "",
            "verified_ca": self.verified_ca or "",
            "last_payment": self.last_payment if self.last_payment is not None else "",
            "last_paid_on": self.last_paid_on or "",
        }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _blocked_page(status: int, text: str) -> bool:
    head = (text or "")[:2000].lower()
    if status == 406:
        return True
    if "custom-page.apptrana.com" in head:
        return True
    return "request was blocked" in head and "suspicious" in head


def _site_down(status: int, text: str) -> bool:
    if status in (502, 503, 504):
        return True
    head = (text or "")[:4000].lower()
    hints = (
        "under maintenance",
        "temporarily unavailable",
        "service unavailable",
        "site is down",
        "system is down",
        "we'll be back",
        "we will be back",
    )
    return any(hint in head for hint in hints)


def _session_headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-IN,en;q=0.9",
        "Cache-Control": "max-age=0",
        "Upgrade-Insecure-Requests": "1",
    }


def _post_headers(pay_url: str) -> dict[str, str]:
    parsed = urlparse(pay_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return {
        "Referer": pay_url,
        "Origin": origin,
        "Content-Type": "application/x-www-form-urlencoded",
        "Cache-Control": "max-age=0",
    }


def _form_post_data(fields: dict[str, str], ca_no: str, mobile: str, email: str, captcha_sum: int) -> dict[str, str]:
    data = dict(fields)
    data["txtcano"] = ca_no
    data["txtmobile"] = mobile
    data["txtemail"] = email
    data["TxtImgVer"] = str(captcha_sum)
    data["btnpay"] = "Pay Now"
    for key in ("__EVENTTARGET", "__EVENTARGUMENT", "__LASTFOCUS"):
        data.setdefault(key, "")
    placeholder = "enter addition of two numbers"
    if data.get("TxtImgVer", "").strip().lower() == placeholder:
        data["TxtImgVer"] = str(captcha_sum)
    return data


class NdplClient:
    def __init__(
        self,
        pay_url: str = PAY_URL_DEFAULT,
        mobile: str = "",
        email: str = "",
        timeout: int = 30,
        captcha_tries: int = DEFAULT_CAPTCHA_TRIES,
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
        self.restarts = 0
        self.blocked_note: str | None = None
        self.last_captcha_attempts = 0
        self.captcha_images_attempted = 0
        self.captcha_solved = 0
        self.session = requests.Session()
        self.session.headers.update(_session_headers())

    def fetch(self, ca_no: str, on_progress=None) -> BillResult:
        checked = _now()
        if self.blocked_note:
            self._log("blocked-skip", ca=ca_no)
            self._emit(on_progress, ca_no, 0, "blocked")
            return self._error(ca_no, self.blocked_note, checked)
        self.last_captcha_attempts = 0
        self.captcha_images_attempted = 0
        self.captcha_solved = 0
        try:
            result = self._fetch_with_site_retries(ca_no, checked, on_progress)
            if result.status in ("Paid", "Unpaid") and not _bill_result_verified(result, ca_no):
                self._log("verify-retry", ca=ca_no)
                self.restart()
                retry = self._fetch_with_site_retries(ca_no, checked, on_progress)
                if _bill_result_verified(retry, ca_no):
                    return retry
                if retry.status in ("Paid", "Unpaid"):
                    return self._error(
                        ca_no,
                        "Bill page did not show all fields after a fresh retry.",
                        checked,
                    )
                return retry
            return result
        except WafBlocked:
            self.blocked_note = _BLOCKED_MESSAGE
            self._log("waf", ca=ca_no)
            return self._error(ca_no, self.blocked_note, checked)
        except SiteDown as exc:
            self._log("site-down", ca=ca_no, error=str(exc))
            return self._error(ca_no, str(exc), checked)
        except _RETRY_NETWORK as exc:
            self._log("session-restart", ca=ca_no, error=str(exc))
            self.restart()
            try:
                return self._fetch_with_site_retries(ca_no, checked, on_progress)
            except WafBlocked:
                self.blocked_note = _BLOCKED_MESSAGE
                return self._error(ca_no, self.blocked_note, checked)
            except SiteDown as exc2:
                return self._error(ca_no, str(exc2), checked)
            except requests.Timeout as exc2:
                return self._error(ca_no, _timeout_note(exc2), checked)
            except requests.RequestException as exc2:
                return self._error(
                    ca_no,
                    f"Network error after restarting the session: {exc2}",
                    checked,
                )
            except Exception as exc2:
                return self._error(
                    ca_no,
                    f"Bill check failed after restarting the session: {exc2}",
                    checked,
                )
        except requests.Timeout as exc:
            return self._error(ca_no, _timeout_note(exc), checked)
        except requests.RequestException as exc:
            return self._error(ca_no, f"Network error: {exc}", checked)
        except Exception as exc:
            return self._error(ca_no, f"Bill check failed: {exc}", checked)

    def _captcha_dir(self) -> Path | None:
        """Keep captcha images out of the page-debug folder and out of the source tree."""
        if self.debug_dir is None:
            return None
        return Path(self.debug_dir) / "captcha"

    def restart(self) -> None:
        """Open a fresh session after the current one dies. Patched test methods are kept."""
        old = self.session
        try:
            old.close()
        except Exception:
            pass
        fresh = requests.Session()
        fresh.headers.update(_session_headers())
        for name in ("get", "post"):
            if name in old.__dict__:
                setattr(fresh, name, old.__dict__[name])
        self.session = fresh
        self.restarts += 1
        self._log("session-restarted", restarts=self.restarts)

    def _fetch_with_site_retries(self, ca_no: str, checked: str, on_progress=None) -> BillResult:
        for attempt in range(_SITE_ERROR_ATTEMPTS):
            try:
                result = self._fetch_once(ca_no, checked, on_progress)
            except SiteErrorPage:
                result = self._error(ca_no, SITE_ERROR_NOTE, checked)
            if result.status != "Error" or SITE_ERROR_NOTE not in (result.note or ""):
                return result
            last = result.note or SITE_ERROR_NOTE
            if attempt + 1 >= _SITE_ERROR_ATTEMPTS:
                break
            self._log("site-error-retry", ca=ca_no, attempt=attempt + 1)
            self.restart()
            self._pause(_SITE_ERROR_BACKOFF[min(attempt, len(_SITE_ERROR_BACKOFF) - 1)])
        return self._error(
            ca_no,
            f"{last} (after {_SITE_ERROR_ATTEMPTS} tries with a fresh session).",
            checked,
        )

    def diagnose(self, ca_no: str) -> dict:
        """One verbose Fast-mode check. Saves a JSON report under debug_dir."""
        steps: list[dict] = []
        checked = _now()

        def step(name: str, **fields) -> None:
            steps.append({"step": name, **fields})

        step("mode", value="fast")
        try:
            page = self._get(self.pay_url)
        except Exception as exc:
            step("open_form", ok=False, error=str(exc))
            return self._diagnose_report(ca_no, checked, steps, self._error(ca_no, str(exc), checked))

        step("open_form", ok=True, http_status=200, form_found="txtcano" in page.lower())
        fields = parse_form_fields(page)
        step("form_fields", names=sorted(fields.keys()))
        image_url = _captcha_image_url(page, self.pay_url)
        step("captcha_url", url=image_url)
        try:
            image = self._captcha_bytes(image_url)
            total, _ocr_meta = solve_captcha(image)
        except TesseractNotFoundError:
            result = self._error(ca_no, _TESSERACT_NOTE, checked)
            step("captcha_ocr", ok=False, error="tesseract missing")
            return self._diagnose_report(ca_no, checked, steps, result)
        except Exception as exc:
            result = self._error(ca_no, f"Captcha failed: {exc}", checked)
            step("captcha_ocr", ok=False, error=str(exc))
            return self._diagnose_report(ca_no, checked, steps, result)

        step("captcha_ocr", ok=total is not None, sum=total)
        if total is None:
            result = self._error(ca_no, "Captcha image was not confident", checked)
            return self._diagnose_report(ca_no, checked, steps, result)

        data = _form_post_data(fields, ca_no, self.mobile, self.email, total)
        step("post_fields", names=sorted(data.keys()))
        try:
            response = self.session.post(
                self.pay_url,
                data=data,
                timeout=self.timeout,
                allow_redirects=True,
                headers=_post_headers(self.pay_url),
            )
        except requests.RequestException as exc:
            result = self._error(ca_no, f"Submit failed: {exc}", checked)
            step("post", ok=False, error=str(exc))
            return self._diagnose_report(ca_no, checked, steps, result)

        html = response.text or ""
        step(
            "post",
            ok=True,
            http_status=response.status_code,
            final_url=response.url,
            site_error=site_error_html(html, response.url),
        )
        if site_error_html(html, response.url):
            result = self._error(ca_no, SITE_ERROR_NOTE, checked)
            return self._diagnose_report(ca_no, checked, steps, result)

        html = self._maybe_follow(html)
        parsed = parse_bill_html(html, expected_ca=ca_no)
        result = self._from_parse(ca_no, parsed, checked)
        step("parse", status=result.status, amount=result.amount, note=result.note)
        return self._diagnose_report(ca_no, checked, steps, result)

    def _diagnose_report(self, ca_no: str, checked: str, steps: list[dict], result: BillResult) -> dict:
        report = {
            "ca_no": ca_no,
            "checked_at": checked,
            "mode": "fast",
            "steps": steps,
            "result": result.as_dict(),
        }
        if self.debug_dir is None:
            report["report_path"] = ""
            return report
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        safe = "".join(ch for ch in ca_no if ch.isdigit()) or "unknown"
        path = self.debug_dir / f"diagnose-{safe}-{stamp}.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        report["report_path"] = str(path)
        return report

    def _emit(self, on_progress, ca_no: str, captcha_try: int, phase: str) -> None:
        if on_progress is None:
            return
        try:
            on_progress(
                {
                    "ca_no": ca_no,
                    "captcha_try": captcha_try,
                    "captcha_tries": self.captcha_tries,
                    "phase": phase,
                }
            )
        except Exception:
            return

    def _fetch_once(self, ca_no: str, checked: str, on_progress=None) -> BillResult:
        self._emit(on_progress, ca_no, 0, "opening")
        try:
            page = self._get(self.pay_url)
        except (WafBlocked, SiteDown):
            raise
        except _RETRY_NETWORK:
            raise
        except requests.RequestException as exc:
            return self._error(ca_no, f"Network error: {exc}", checked)

        if "txtcano" not in page.lower():
            return self._error(ca_no, "Pay-bill form not found on the page", checked)

        fields = parse_form_fields(page)
        image_url = _captcha_image_url(page, self.pay_url)
        last_note = "Captcha image was not confident, so it was not submitted"
        for attempt in range(self.captcha_tries):
            self.last_captcha_attempts = attempt + 1
            self.captcha_images_attempted += 1
            self._emit(on_progress, ca_no, attempt + 1, "captcha")
            if attempt:
                captcha_pause(attempt)
                self._pause(min(1.2, 0.35 * attempt))
                try:
                    page = self._get(self.pay_url)
                except (WafBlocked, SiteDown):
                    raise
                except _RETRY_NETWORK:
                    raise
                except requests.RequestException as exc:
                    return self._error(ca_no, f"Network error: {exc}", checked)
                if "txtcano" not in page.lower():
                    return self._error(ca_no, "Pay-bill form not found on the page", checked)
                fields = parse_form_fields(page)
                image_url = _captcha_image_url(page, self.pay_url)
            try:
                image = self._captcha_bytes(image_url, refresh=attempt > 0)
            except (WafBlocked, SiteDown):
                raise
            except _RETRY_NETWORK:
                raise
            except requests.RequestException as exc:
                return self._error(ca_no, f"Captcha image failed: {exc}", checked)
            try:
                total, ocr_meta = solve_captcha(image)
            except TesseractNotFoundError:
                return self._error(ca_no, _TESSERACT_NOTE, checked)
            except Exception as exc:
                last_note = f"Captcha image could not be read ({exc})"
                save_captcha_attempt(
                    ca_no,
                    attempt + 1,
                    image,
                    {"error": str(exc)},
                    outcome="error",
                    directory=self._captcha_dir(),
                )
                self._log(
                    "captcha-try",
                    ca=ca_no,
                    captcha_try=attempt + 1,
                    tries=self.captcha_tries,
                    guesses=[],
                    chosen=None,
                    site="read_error",
                )
                continue
            if total is None:
                last_note = "The captcha was not clear, so the number was not sent"
                save_captcha_attempt(
                    ca_no,
                    attempt + 1,
                    image,
                    ocr_meta,
                    outcome="skip",
                    directory=self._captcha_dir(),
                )
                self._log(
                    "captcha-try",
                    ca=ca_no,
                    captcha_try=attempt + 1,
                    tries=self.captcha_tries,
                    guesses=ocr_meta.get("guesses"),
                    chosen=ocr_meta.get("chosen_pair"),
                    site="not_sent",
                )
                continue
            self._log(
                "captcha-try",
                ca=ca_no,
                captcha_try=attempt + 1,
                tries=self.captcha_tries,
                guesses=ocr_meta.get("guesses"),
                chosen=ocr_meta.get("chosen_sum"),
                site="submitting",
            )
            data = _form_post_data(fields, ca_no, self.mobile, self.email, total)
            try:
                response = self.session.post(
                    self.pay_url,
                    data=data,
                    timeout=self.timeout,
                    allow_redirects=True,
                    headers=_post_headers(self.pay_url),
                )
            except _RETRY_NETWORK:
                raise
            except requests.RequestException as exc:
                return self._error(ca_no, f"Submit failed: {exc}", checked)
            try:
                html = self._response_text(response, final_url=response.url)
            except (WafBlocked, SiteDown):
                raise
            except SiteErrorPage:
                return self._error(ca_no, SITE_ERROR_NOTE, checked)
            except requests.HTTPError as exc:
                return self._error(ca_no, f"Tata Power returned {exc}.", checked)
            html = self._maybe_follow(html)
            self._emit(on_progress, ca_no, attempt + 1, "reading")
            parsed = parse_bill_html(html, expected_ca=ca_no)
            if parsed.site_error:
                return self._error(ca_no, SITE_ERROR_NOTE, checked)
            if parsed.status in ("Paid", "Unpaid") and not bill_parse_verified(parsed, ca_no):
                last_note = "Bill page was missing amount, due date, or bill number"
                continue
            if parsed.captcha_error:
                last_note = "Captcha was rejected by the site"
                save_captcha_attempt(
                    ca_no,
                    attempt + 1,
                    image,
                    {**ocr_meta, "site": "captcha_rejected"},
                    outcome="wrong",
                    directory=self._captcha_dir(),
                )
                self._log(
                    "captcha-try",
                    ca=ca_no,
                    captcha_try=attempt + 1,
                    tries=self.captcha_tries,
                    chosen=total,
                    site="wrong",
                )
                continue
            if parsed.save_debug:
                parsed.note = self._attach_debug(ca_no, html, parsed.note)
            self._record_history(parsed, html, ca_no)
            save_captcha_attempt(
                ca_no,
                attempt + 1,
                image,
                {**ocr_meta, "site": "ok", "status": parsed.status},
                outcome="ok",
                directory=self._captcha_dir(),
            )
            self.captcha_solved += 1
            result = self._from_parse(ca_no, parsed, checked)
            self._log(
                "result",
                ca=ca_no,
                status=result.status,
                amount=result.amount,
                note=result.note,
                captcha_try=attempt + 1,
            )
            return result
        return self._error(
            ca_no,
            f"Captcha could not be solved after {self.captcha_tries} tries. {last_note}",
            checked,
        )

    def _record_history(self, parsed: BillParse, html: str, ca_no: str) -> None:
        """GET the last-payment tab when the bill page names one. Never a pay click."""
        if parsed.status not in ("Paid", "Unpaid"):
            return
        url = _history_popup_url(html)
        if not url or not _safe_result_url(url, self.pay_url):
            return
        absolute = urljoin(self.pay_url, url.strip())
        try:
            page = self._get(absolute)
        except Exception as exc:
            self._log("history-skip", ca=ca_no, error=str(exc))
            return
        amount, paid_on = parse_payment_history(page, expected_ca=ca_no)
        if amount is None or not paid_on:
            self._log("history-unread", ca=ca_no)
            return
        parsed.last_payment = amount
        parsed.last_paid_on = paid_on

    def _maybe_follow(self, html: str) -> str:
        """Read a same-origin bill tab when the POST body is only a launcher.

        Payment hosts are never requested. The history tab is ignored when the
        bill table is already in this response.
        """
        if _already_bill_page(html):
            return html
        followed = 0
        for url in _popup_urls(html):
            if followed >= 2:
                break
            if not _safe_result_url(url, self.pay_url):
                self._log("popup-skip", url=url)
                continue
            absolute = urljoin(self.pay_url, url.strip())
            self._log("popup-open", url=absolute)
            try:
                page = self._get(absolute)
            except SiteErrorPage:
                self._log("popup-skip", url=absolute, reason="site-error")
                continue
            followed += 1
            if _already_bill_page(page) or "bill details" in page.lower():
                return page
        return html

    def _captcha_bytes(self, image_url: str, *, refresh: bool = False) -> bytes:
        """Load the captcha tied to the current form; ``refresh`` mimics Change Image."""
        url = image_url
        if refresh:
            nonce = str(int(time.time() * 1000))
            joiner = "&" if "?" in image_url else "?"
            url = f"{image_url}{joiner}rand={nonce}"
        return self._get_bytes(url, referer=self.pay_url)

    def _get(self, url: str, *, referer: str | None = None) -> str:
        if referer:
            response = self.session.get(
                url, timeout=self.timeout, headers={"Referer": referer}
            )
        else:
            response = self.session.get(url, timeout=self.timeout)
        return self._response_text(response, final_url=response.url)

    def _get_bytes(self, url: str, *, referer: str | None = None) -> bytes:
        if referer:
            response = self.session.get(
                url, timeout=self.timeout, headers={"Referer": referer}
            )
        else:
            response = self.session.get(url, timeout=self.timeout)
        # Captcha PNG bytes are not HTML; do not run stderr detection on them.
        if _blocked_page(response.status_code, (response.text or "")[:2000]):
            raise WafBlocked(_BLOCKED_MESSAGE)
        if response.status_code >= 400:
            raise requests.HTTPError(f"HTTP {response.status_code}")
        return response.content

    @staticmethod
    def _response_text(response: requests.Response, final_url: str = "") -> str:
        text = response.text or ""
        url = final_url or getattr(response, "url", "") or ""
        if site_error_html(text, url):
            raise SiteErrorPage(SITE_ERROR_NOTE)
        if _blocked_page(response.status_code, text):
            raise WafBlocked(_BLOCKED_MESSAGE)
        if _site_down(response.status_code, text):
            raise SiteDown(
                f"Tata Power's site looks down or under maintenance (HTTP {response.status_code})."
            )
        if response.status_code >= 400:
            raise requests.HTTPError(f"HTTP {response.status_code}")
        return text

    def _attach_debug(self, ca_no: str, html: str, note: str) -> str:
        if self.debug_dir is None:
            return note + " Debug HTML was not saved (no debug folder)."
        try:
            html_path, _png_path = save_page_debug(self.debug_dir, ca_no, html)
        except Exception as exc:
            return note + f" Debug files could not be saved ({exc})."
        return f"{note} Saved debug/{html_path.name} and .png."

    def _log(self, event: str, **fields) -> None:
        if self.log is None:
            return
        try:
            self.log(event, **fields)
        except Exception:
            return

    @staticmethod
    def _from_parse(ca_no: str, parsed: BillParse, checked: str) -> BillResult:
        accepted = parsed.status in ("Paid", "Unpaid")
        return BillResult(
            ca_no=ca_no,
            status=parsed.status,
            amount=parsed.amount if accepted else None,
            due_date=parsed.due_date if accepted else None,
            consumer_name=parsed.consumer_name if accepted else None,
            note=parsed.note,
            checked_at=checked,
            bill_no=parsed.bill_no if accepted else None,
            bill_date=parsed.bill_date if accepted else None,
            exact_bill=parsed.exact_bill if accepted else None,
            verified_ca=parsed.page_ca if accepted else None,
            last_payment=parsed.last_payment if accepted else None,
            last_paid_on=parsed.last_paid_on if accepted else None,
        )

    @staticmethod
    def _error(ca_no: str, note: str, checked: str) -> BillResult:
        return BillResult(
            ca_no=ca_no,
            status="Error",
            amount=None,
            due_date=None,
            consumer_name=None,
            note=note,
            checked_at=checked,
        )


_BLOCKED_MESSAGE = (
    "Tata Power blocked this computer (HTTP 406, AppTrana: "
    "request was blocked due to suspicious behavior). "
    "This CA was not checked. Later CAs in this run are marked the same "
    "without calling the site again. Run the app on the owner's Windows PC."
)

_TESSERACT_NOTE = (
    "Tesseract is not installed, so the captcha image cannot be read. "
    "The portable app looks for tesseract\\tesseract.exe next to NarelaRentApp.exe. "
    "Set TESSERACT_CMD if it lives somewhere else."
)


def _bill_result_verified(result: BillResult, expected_ca: str) -> bool:
    if result.status not in ("Paid", "Unpaid"):
        return True
    if result.verified_ca and result.verified_ca != expected_ca:
        return False
    if result.status == "Paid" and result.amount == 0:
        return True
    if result.amount is None or result.exact_bill in (None, ""):
        return False
    if not result.due_date or not result.bill_no:
        return False
    return True


def _timeout_note(exc: BaseException) -> str:
    return f"Timed out waiting for Tata Power ({exc.__class__.__name__})."


def _history_popup_url(html: str) -> str | None:
    titled = re.finditer(
        r"""window\.open\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]*)['"]""",
        html,
        re.I,
    )
    for match in titled:
        url, title = match.group(1), match.group(2)
        if "history" in url.lower() or "history" in title.lower():
            return url
    for url in _popup_urls(html):
        if "history" in url.lower():
            return url
    return None


def _already_bill_page(html: str) -> bool:
    low = html.lower()
    return "bill details" in low and "exact bill" in low and "outstanding" in low


def _popup_urls(html: str) -> list[str]:
    patterns = (
        r"""window\.open\(\s*['"]([^'"]+)['"]""",
        r"""(?:window\.)?location(?:\.href)?\s*=\s*['"]([^'"]+)['"]""",
        r"""<meta[^>]+http-equiv=["']refresh["'][^>]+content=["'][^"']*url=([^"';]+)""",
        r"""<a\b[^>]*\btarget=["']_blank["'][^>]*\bhref=["']([^"']+)["']""",
        r"""<a\b[^>]*\bhref=["']([^"']+)["'][^>]*\btarget=["']_blank["']""",
    )
    found: list[str] = []
    for pattern in patterns:
        found.extend(re.findall(pattern, html, flags=re.I))
    found.sort(key=lambda url: ("history" in url.lower(), url))
    return found


def _safe_result_url(url: str, pay_url: str) -> bool:
    raw = (url or "").strip()
    if not raw or raw.lower().startswith(("javascript:", "mailto:", "#")):
        return False
    absolute = urljoin(pay_url, raw)
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host != "tatapower-ddl.com":
        return False
    path = parsed.path.lower()
    if "img.aspx" in path:
        return False
    blob = path + " " + parsed.query.lower()
    if any(marker in blob for marker in _GATEWAY_MARKERS):
        return False
    if "/billpay/" not in path and not path.rstrip("/").endswith("/billpay"):
        return False
    return True


def _captcha_image_url(html: str, pay_url: str) -> str:
    patterns = (
        r"""<img\b[^>]*\bid=["']Img["'][^>]*\bsrc=["']([^"']+)["']""",
        r"""<img\b[^>]*\bsrc=["']([^"']+)["'][^>]*\bid=["']Img["']""",
        r"""src=["']([^"']*(?:capt|Img)\.aspx[^"']*)["']""",
    )
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            src = match.group(1).split("?", 1)[0]
            return urljoin(pay_url, src)
    return urljoin(pay_url, "capt.aspx")


def save_page_debug(folder: Path, ca_no: str, html: str) -> tuple[Path, Path]:
    """Write the response HTML and a text snapshot PNG. Neither is a payment."""
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    safe = "".join(ch for ch in ca_no if ch.isdigit()) or "unknown"
    stem = f"{safe}-{stamp}"
    html_path = folder / f"{stem}.html"
    extra = 2
    while html_path.exists():
        html_path = folder / f"{stem}-{extra}.html"
        extra += 1
    png_path = html_path.with_suffix(".png")
    html_path.write_text(html, encoding="utf-8")
    _write_snapshot(html, png_path)
    return html_path, png_path


def _write_snapshot(html: str, png_path: Path) -> None:
    lines = [line.strip() for line in visible_text(html).splitlines() if line.strip()][:40]
    if not lines:
        lines = ["(empty page)"]
    try:
        from PIL import Image, ImageDraw, ImageFont

        font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        font = ImageFont.truetype(str(font_path), 16) if font_path.is_file() else ImageFont.load_default()
        image = Image.new("RGB", (980, 24 + 22 * len(lines)), "white")
        draw = ImageDraw.Draw(image)
        for index, line in enumerate(lines):
            safe_line = line.encode("ascii", "replace").decode("ascii")[:140]
            draw.text((10, 8 + index * 22), safe_line, fill="black", font=font)
        image.save(png_path, format="PNG")
    except Exception:
        png_path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
            b"\x00\x00\x00\x03\x00\x01\x00\x05\xfe\x02\xfe\x00\x00\x00\x00IEND\xaeB`\x82"
        )
