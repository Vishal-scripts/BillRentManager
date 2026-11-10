"""Background bill queue. One CA at a time, with a lock and a live progress snapshot."""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from billcheck.captcha import captcha_tries_from_env  # noqa: E402

from ownerapp.alerts import sync_alerts
from ownerapp.contacts import normalize_mobile, receipt_message, receipt_ready
from ownerapp.db import Database, utc_now
from ownerapp.doctor import run_checks
from ownerapp.demo_bills import demo_fetch
from ownerapp.messages import explain_failure
from ownerapp.paths import data_dir
from ownerapp.queue_logic import dedupe_jobs, merge_bill, select_cas

STEP_LABEL = {
    "waiting": "Waiting",
    "opening": "Opening site",
    "captcha": "Solving captcha",
    "reading": "Reading bill",
    "done": "Done",
    "error": "Error",
    "stopped": "Stopped",
}

IDLE = {
    "state": "Idle",
    "mode": "",
    "progress": "",
    "current_ca": "",
    "current_name": "",
    "captcha_try": 0,
    "captcha_tries": 6,
    "phase": "",
    "summary": "",
    "done": 0,
    "total": 0,
    "percent": 0,
    "message": "",
    "items": [],
    "log": [],
    "started_at": "",
    "started_epoch": 0,
    "elapsed_seconds": 0,
    "eta_seconds": None,
}


class BillService:
    def __init__(
        self,
        db: Database,
        *,
        demo: bool = False,
        fetch=None,
        today=None,
        gap: float | None = None,
        log_path: Path | None = None,
    ) -> None:
        self.db = db
        self.demo = demo
        self.fetch = fetch
        self.today = today or date.today
        self.gap = gap
        self.log_path = log_path
        self.progress = dict(IDLE)
        self._subs: list[queue.Queue] = []
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._busy = threading.Lock()
        self._thread: threading.Thread | None = None
        self._client = None
        self._browser_client = None

    def start(self) -> None:
        if self.demo:
            self.db.set_setting("demo", "true")
        resumed = self.db.requeue_checking()
        if resumed:
            self._log("resume", jobs=resumed)
        self.db.rollover(self._today())
        sync_alerts(self.db, self._today())
        self._thread = threading.Thread(target=self._loop, name="bill-queue", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def wait_until_idle(self, timeout: float = 8) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            quiet = not self._busy.locked() and not self.db.open_job_cas()
            snap = self.snapshot()
            if quiet and snap.get("state") == "Done" and snap.get("summary"):
                return True
            time.sleep(0.02)
        return False

    def snapshot(self) -> dict:
        snap = dict(self.progress)
        items = [dict(item) for item in snap.get("items") or []]
        snap["items"] = items
        snap["log"] = list(snap.get("log") or [])
        started = snap.get("started_epoch") or 0
        if started and snap.get("state") == "Running":
            elapsed = max(0, int(time.time() - started))
            snap["elapsed_seconds"] = elapsed
            done = sum(1 for item in items if item.get("step") in ("done", "error", "stopped"))
            remain = max(0, len(items) - done)
            snap["eta_seconds"] = int(elapsed / done * remain) if done else None
        return snap

    def cancel(self) -> dict:
        self._cancel.set()
        self._wake.set()
        if self.progress.get("state") == "Running":
            self.progress["message"] = "Stopping after the current CA."
            self._publish()
        return {"ok": True}

    def subscribe(self) -> queue.Queue:
        box: queue.Queue = queue.Queue()
        self._subs.append(box)
        box.put(self.snapshot())
        return box

    def unsubscribe(self, box: queue.Queue) -> None:
        try:
            self._subs.remove(box)
        except ValueError:
            return

    def enqueue(self, mode: str, ca_no: str = "", selected: list[str] | None = None) -> dict:
        blocked = self._receipt_error()
        if blocked:
            return {"ok": False, "error": blocked, "added": 0}
        self._cancel.clear()
        rows = self._targets()
        picked = select_cas(rows, mode, {"ca_no": ca_no, "selected": selected or []})
        if not picked["ok"]:
            return {"ok": False, "error": picked["error"], "added": 0}
        split = dedupe_jobs(self.db.open_job_cas(), picked["cas"])
        if not split["add"]:
            if picked["cas"]:
                message = f"Nothing new to queue. Already queued: {len(split['skipped'])}."
            else:
                message = "No matching CA."
            return {"ok": True, "added": 0, "skipped": split["skipped"], "message": message}
        self.db.enqueue_jobs(split["add"], mode)
        message = f"Queued {len(split['add'])} CA."
        if split["skipped"]:
            message += f" Skipped {len(split['skipped'])} already in the queue."
        if self.progress.get("state") in ("Idle", "Done", "Queued", ""):
            self.progress = {
                **IDLE,
                "state": "Queued",
                "mode": mode,
                "message": message,
                "total": len(split["add"]),
                "progress": f"0/{len(split['add'])}",
            }
        self._wake.set()
        self._publish()
        return {"ok": True, "added": len(split["add"]), "skipped": split["skipped"], "message": message}

    def _targets(self) -> list[dict]:
        bills = {row["ca_no"]: row for row in self.db.bill_rows()}
        out = []
        for tenant in self.db.list_tenants():
            ca = tenant["ca_no"]
            if not ca:
                continue
            bill = bills.get(ca, {})
            out.append(
                {
                    "ca_no": ca,
                    "name": tenant["name"],
                    "tenant_id": tenant["id"],
                    "status": bill.get("status") or "",
                    "check_state": bill.get("check_state") or "",
                    "active": bool(tenant["active"]),
                }
            )
        return out

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._maybe_auto()
                self.db.rollover(self._today())
                if not self._busy.locked():
                    self.db.requeue_stale_checking()
                if not self.db.open_job_cas():
                    self._wake.wait(1.0)
                    self._wake.clear()
                    continue
            except Exception as exc:
                if self._stop.is_set():
                    break
                self._log("loop-error", error=str(exc))
                self._wake.wait(0.5)
                self._wake.clear()
                continue
            if not self._busy.acquire(blocking=False):
                self._wake.wait(0.5)
                self._wake.clear()
                continue
            try:
                self._run_once()
            except Exception as exc:
                self._log("run-error", error=str(exc))
                self.progress = {**self.progress, "state": "Idle", "message": str(exc)}
                self._publish()
            finally:
                self._busy.release()

    def _run_once(self) -> None:
        jobs = self.db.claim_queued(utc_now())
        if not jobs:
            return
        self._client = None
        self._browser_client = None
        total = len(jobs)
        paid = unpaid = errors = stopped = 0
        captcha_attempts = 0
        captcha_solved = 0
        started_epoch = time.time()
        started_at = utc_now()
        items = [
            {
                "ca_no": job["ca_no"],
                "name": job.get("name") or "",
                "step": "waiting",
                "note": "",
                "plain": "",
                "fix": "",
                "failed_step": "",
            }
            for job in jobs
        ]
        log: list[str] = [f"Started {total} CA."]
        self.progress = {
            **IDLE,
            "state": "Running",
            "mode": jobs[0].get("mode") or "",
            "total": total,
            "progress": f"0/{total}",
            "captcha_tries": self._tries(),
            "items": items,
            "log": log,
            "started_at": started_at,
            "started_epoch": started_epoch,
        }
        self._publish()
        self._log("run-start", jobs=total, mode=jobs[0].get("mode"))
        for index, job in enumerate(jobs):
            if self._cancel.is_set():
                stopped += self._stop_rest(jobs[index:], items, log, index)
                break
            pause = self._pause()
            if index and pause:
                time.sleep(pause)
            if self._cancel.is_set():
                stopped += self._stop_rest(jobs[index:], items, log, index)
                break
            self._mark(items, index, "opening", job, index, total)
            log.append(f"Opening site for {job.get('name') or job['ca_no']}.")

            job_captcha = {"tries": 0}

            def on_progress(event, job=job, index=index):
                self.db.touch_job(job["id"])
                phase = event.get("phase") or "opening"
                step = phase if phase in STEP_LABEL else "opening"
                if step == "blocked":
                    step = "error"
                try_no = event.get("captcha_try") or 0
                self.progress["captcha_try"] = try_no
                self.progress["captcha_tries"] = event.get("captcha_tries") or self._tries()
                if step == "captcha" and try_no:
                    job_captcha["tries"] = max(job_captcha["tries"], try_no)
                self._mark(items, index, step, job, index, total)
                if step == "captcha" and try_no:
                    log.append(f"Solving captcha for {job['ca_no']}, try {try_no} of {self.progress['captcha_tries']}.")

            try:
                result = self._fetch(job["ca_no"], on_progress)
            except Exception as exc:
                result = {
                    "ca_no": job["ca_no"],
                    "status": "Error",
                    "note": f"Bill check failed: {exc}",
                    "checked_at": utc_now(),
                }
            previous = self.db.bill_map().get(job["ca_no"])
            merged = merge_bill(previous, result)
            self.db.save_bill(merged)
            self.db.finish_job(job["id"], merged["check_state"], merged.get("note") or "")
            if self._client:
                captcha_attempts += self._client.captcha_images_attempted
                captcha_solved += self._client.captcha_solved
            else:
                captcha_attempts += job_captcha["tries"]
                if merged["status"] in ("Paid", "Unpaid"):
                    captcha_solved += 1
            status = merged["status"]
            if status == "Paid":
                paid += 1
                self._mark(items, index, "done", job, index + 1, total, note=merged.get("note") or "")
                log.append(f"Done {job['ca_no']}: Paid.")
            elif status == "Unpaid":
                unpaid += 1
                self._mark(items, index, "done", job, index + 1, total, note=merged.get("note") or "")
                log.append(f"Done {job['ca_no']}: Unpaid.")
            else:
                errors += 1
                explained = explain_failure(merged.get("note") or "")
                self._mark(
                    items,
                    index,
                    "error",
                    job,
                    index + 1,
                    total,
                    note=merged.get("note") or "",
                    explained=explained,
                )
                log.append(f"Error {job['ca_no']} at {explained['step']}: {explained['plain']}")
            self._log("result", ca=job["ca_no"], status=status, note=merged.get("note"))
        captcha_rate = int(round(100 * captcha_solved / captcha_attempts)) if captcha_attempts else 100
        log.append(
            f"Captcha: {captcha_attempts} image attempts, {captcha_solved} solved ({captcha_rate}% success rate)."
        )
        summary = (
            f"Run summary: ok={paid + unpaid} paid={paid} unpaid={unpaid} errors={errors} "
            f"captcha_rate={captcha_rate}%"
        )
        if stopped:
            summary += f" stopped={stopped}"
        state = "Stopped" if stopped and not (paid or unpaid or errors) else ("Stopped" if self._cancel.is_set() else "Done")
        if self._cancel.is_set():
            state = "Stopped"
        self.progress = {
            **self.progress,
            "state": state,
            "phase": "done",
            "current_ca": "",
            "current_name": "",
            "done": paid + unpaid + errors + stopped,
            "progress": f"{paid + unpaid + errors + stopped}/{total}",
            "percent": 100,
            "summary": summary,
            "captcha_try": 0,
            "items": items,
            "log": log,
            "elapsed_seconds": int(time.time() - started_epoch),
            "eta_seconds": 0,
        }
        self._publish()
        self.db.add_run(started_at, utc_now(), summary, "\n".join(log))
        self.db.add_alert("run", "Bill refresh finished", summary, f"run:{utc_now()}")
        sync_alerts(self.db, self._today())
        self._log("run-finish", summary=summary)
        time.sleep(0.05)

    def _mark(self, items, index, step, job, done, total, note="", explained=None) -> None:
        item = items[index]
        item["step"] = step
        if note:
            item["note"] = note
        if explained:
            item["plain"] = explained["plain"]
            item["fix"] = explained["fix"]
            item["failed_step"] = explained["step"]
        self.progress.update(
            {
                "items": items,
                "current_ca": "" if step in ("done", "error", "stopped") else job["ca_no"],
                "current_name": "" if step in ("done", "error", "stopped") else (job.get("name") or ""),
                "phase": step,
                "done": done,
                "progress": f"{done}/{total}",
                "percent": int(done * 100 / total) if total else 0,
            }
        )
        self._publish()

    def _stop_rest(self, jobs, items, log, start_index) -> int:
        count = 0
        for offset, job in enumerate(jobs):
            index = start_index + offset
            self.db.finish_job(job["id"], "Stopped", "Stopped before this CA was checked.")
            self.db.set_check_states([job["ca_no"]], "")
            items[index]["step"] = "stopped"
            items[index]["plain"] = "Stopped. This CA was not checked."
            items[index]["fix"] = "Press Retry this to check it again."
            log.append(f"Stopped {job['ca_no']} before opening the site.")
            count += 1
        return count

    def diagnose(self, ca_no: str) -> dict:
        if self.demo or self.fetch is not None:
            return {"ok": False, "error": "Diagnose is only available for live bill checks."}
        email = (self.db.setting("receipt_email") or "").strip()
        mobile = normalize_mobile(self.db.setting("receipt_mobile") or "")
        blocked = receipt_message(email, mobile)
        if blocked:
            return {"ok": False, "error": blocked}
        from billcheck.ndpl import NdplClient

        client = NdplClient(
            mobile=mobile,
            email=email,
            captcha_tries=captcha_tries_from_env(self._tries()),
            debug_dir=data_dir() / "debug",
            log=lambda event, **fields: self._log(event, **fields),
        )
        report = client.diagnose(ca_no)
        report["ok"] = True
        return report

    def _fetch(self, ca_no: str, on_progress) -> dict:
        if self.fetch:
            return self.fetch(ca_no, on_progress)
        if self.demo:
            return demo_fetch(ca_no, on_progress, tries=self._tries(), pause=0.7)
        from billcheck.ndpl import NdplClient

        email = (self.db.setting("receipt_email") or "").strip()
        mobile = normalize_mobile(self.db.setting("receipt_mobile") or "")
        blocked = receipt_message(email, mobile)
        if blocked:
            raise RuntimeError(blocked)
        mode = self._bill_check_mode()
        if mode == "browser":
            return self._browser_fetch(ca_no, on_progress, email, mobile)
        if self._client is None:
            self._client = NdplClient(
                mobile=mobile,
                email=email,
                captcha_tries=captcha_tries_from_env(self._tries()),
                debug_dir=data_dir() / "debug",
                log=lambda event, **fields: self._log(event, **fields),
            )
        result = self._client.fetch(ca_no, on_progress=on_progress).as_dict()
        if mode == "auto" and result.get("status") == "Error":
            browser = self._browser_fetch(ca_no, on_progress, email, mobile)
            if browser.get("status") != "Error":
                return browser
        return result

    def _browser_fetch(self, ca_no: str, on_progress, email: str, mobile: str) -> dict:
        from billcheck.ndpl_browser import NdplBrowserClient

        if self._browser_client is None:
            self._browser_client = NdplBrowserClient(
                mobile=mobile,
                email=email,
                captcha_tries=captcha_tries_from_env(self._tries()),
                debug_dir=data_dir() / "debug",
                log=lambda event, **fields: self._log(event, **fields),
            )
        return self._browser_client.fetch(ca_no, on_progress=on_progress).as_dict()

    def _bill_check_mode(self) -> str:
        raw = (self.db.setting("bill_check_mode") or "auto").strip().lower()
        if raw in ("auto", "fast", "browser"):
            return raw
        return "auto"

    def _receipt_error(self) -> str:
        if self.demo or self.fetch is not None:
            return ""
        email = self.db.setting("receipt_email") or ""
        mobile = self.db.setting("receipt_mobile") or ""
        if receipt_ready(email, mobile):
            return ""
        return receipt_message(email, mobile)

    def _tries(self) -> int:
        try:
            return max(1, min(12, int(self.db.setting("captcha_tries") or "6")))
        except ValueError:
            return 6

    def _pause(self) -> float:
        if self.gap is not None:
            return self.gap
        try:
            delay = float(self.db.setting("request_delay") or "3")
        except (TypeError, ValueError):
            delay = 3.0
        return max(2.0, delay)

    def _today(self) -> date:
        value = self.today() if callable(self.today) else self.today
        return value

    def _maybe_auto(self) -> None:
        settings = self.db.public_settings()
        if not settings["auto_refresh"]:
            return
        now = datetime.now()
        if now.weekday() != int(settings["auto_weekday"]):
            return
        hour, minute = (settings["auto_time"] or "09:00").split(":")
        if (now.hour, now.minute) < (int(hour), int(minute)):
            return
        stamp = now.date().isoformat()
        if self.db.setting("last_auto_date") == stamp:
            return
        if not self._live_checks_ready():
            self._log("auto-skip", day=stamp, reason="setup")
            return
        result = self.enqueue("all")
        if not result.get("added"):
            self._log("auto-skip", day=stamp, reason=result.get("error") or "nothing queued")
            return
        self.db.set_setting("last_auto_date", stamp)
        self._log("auto-refresh", day=stamp, added=result.get("added"))

    def _live_checks_ready(self) -> bool:
        """The same gate the Refresh button uses before a live bill run."""
        if self.demo or self.fetch is not None:
            return True
        report = run_checks(self.db.path)
        if report.get("ok"):
            return True
        self._log("auto-skip", reason=report.get("headline") or "not ready")
        return False

    def _publish(self) -> None:
        snap = self.snapshot()
        for box in list(self._subs):
            try:
                box.put_nowait(snap)
            except queue.Full:
                continue

    def _log(self, event: str, **fields) -> None:
        if not self.log_path:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        line = {"time": utc_now(), "event": event, **fields}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, default=str) + "\n")
