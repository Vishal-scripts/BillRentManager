"""HTTP API and pages for the local rent app."""

from __future__ import annotations

import json
import queue
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from ownerapp.alerts import dashboard, sync_alerts
from ownerapp.notifications import build_notifications, unread_count
from ownerapp.contacts import email_ok, mobile_ok, normalize_mobile
from ownerapp.db import Database
from ownerapp.demo_bills import seed_demo
from ownerapp.doctor import run_checks
from ownerapp.exporter import build_template, build_workbook
from ownerapp.importer import read_upload
from ownerapp.paths import home, phone_url
from ownerapp.rent_logic import month_key, parse_rent
from ownerapp.service import BillService
from ownerapp.users_file import UserDirectory, pin_too_easy

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}

STATIC = Path(__file__).resolve().parent / "static"


def _import_duplicate(existing: list[dict], row: dict) -> bool:
    ca = row.get("ca_no") or ""
    name = str(row.get("name") or "").strip().lower()
    room = str(row.get("room") or "").strip().lower()
    for item in existing:
        if ca and item.get("ca_no") == ca:
            return True
        if name and name == str(item.get("name") or "").strip().lower() and room == str(item.get("room") or "").strip().lower():
            return True
    return False


def create_app(
    db_path: Path,
    *,
    pin: str | None = None,
    demo: bool = False,
    fetch=None,
    today=None,
    gap: float | None = 0.0,
    log_path: Path | None = None,
    port: int = 8765,
    users_path: Path | None = None,
    secure_cookie: bool = False,
) -> FastAPI:
    db = Database(db_path)
    secret = db.ensure_secret()
    users = UserDirectory(Path(users_path) if users_path else home() / "users.env")
    users.ensure(pin)
    clock = today or date.today
    if demo:
        seed_demo(db, clock() if callable(clock) else clock)
        db.set_setting("demo", "true")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service.start()
        yield
        app.state.service.stop()
        app.state.db.close()

    app = FastAPI(title="Bill Rent Manager", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        same_site="lax",
        https_only=secure_cookie,
        max_age=60 * 60 * 24 * 14,
    )
    app.state.db = db
    app.state.today = clock
    app.state.service = BillService(db, demo=demo, fetch=fetch, today=clock, gap=gap, log_path=log_path)
    app.state.port = port
    app.state.users = users
    app.state.secure_cookie = secure_cookie
    app.state.public_scheme = "https" if secure_cookie else "http"
    users_file = Path(users_path) if users_path else home() / "users.env"
    app.state.phone_flag = users_file.parent / "phone-access.on"
    app.state.live_bills = fetch is None and not demo
    app.state.doctor_cache = (0.0, None)

    def current_day() -> date:
        value = app.state.today() if callable(app.state.today) else app.state.today
        return value

    def signed_in(request: Request) -> dict:
        user = app.state.users.get(str(request.session.get("username") or ""))
        if not request.session.get("ok") or not user:
            request.session.clear()
            raise HTTPException(status_code=401, detail="Sign in with your username and PIN.")
        request.session["role"] = user["role"]
        request.state.user = user
        return user

    def admin_only(user: dict = Depends(signed_in)) -> dict:
        if user["role"] != "admin":
            raise HTTPException(status_code=403, detail="Only an admin can do that.")
        return user

    def client_is_local(request: Request) -> bool:
        host = request.client.host if request.client else ""
        return host in LOCAL_HOSTS

    def require_local(request: Request) -> None:
        """Setup, backup, and restore stay on this computer even when phones can connect."""
        if client_is_local(request):
            return
        raise HTTPException(
            status_code=403,
            detail=(
                "Open Bill Rent Manager on the computer it runs on to do this. "
                "A phone on the Wi-Fi cannot set up the admin, download a backup, or restore one."
            ),
        )

    def remember(request: Request, user: dict) -> None:
        request.session["ok"] = True
        request.session["username"] = user["username"]
        request.session["role"] = user["role"]

    def doctor_report() -> dict:
        cached_at, cached = app.state.doctor_cache
        if cached and time.time() - cached_at < 30:
            return cached
        report = run_checks(Path(db_path))
        report["port"] = app.state.port
        report["checks"].append(
            {
                "name": "Port",
                "ok": True,
                "detail": f"This app is using port {app.state.port}.",
                "fix": "",
            }
        )
        app.state.doctor_cache = (time.time(), report)
        return report

    @app.get("/api/health")
    def health():
        return {"ok": True, "demo": demo}

    @app.post("/api/login")
    def login(request: Request, body: dict):
        user, status = app.state.users.authenticate(str(body.get("username") or ""), str(body.get("pin") or ""))
        if status == "locked":
            raise HTTPException(status_code=429, detail="Too many tries. This username is locked for 5 minutes.")
        if not user:
            raise HTTPException(status_code=401, detail="Wrong username or PIN.")
        remember(request, user)
        return {"ok": True, "username": user["username"], "role": user["role"]}

    @app.post("/api/logout")
    def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    def session_info(request: Request) -> dict:
        app.state.users.reload()
        needs_setup = not app.state.users.users
        user = None
        if request.session.get("ok") and not needs_setup:
            user = app.state.users.get(str(request.session.get("username") or ""))
        role = user["role"] if user else ""
        return {
            "ok": bool(user) and not needs_setup,
            "demo": app.state.db.public_settings()["demo"],
            "needs_setup": needs_setup,
            "phone_url": phone_url(app.state.port, app.state.public_scheme),
            "local_url": f"{app.state.public_scheme}://127.0.0.1:{app.state.port}",
            "phone_access": app.state.phone_flag.is_file(),
            "https": bool(app.state.secure_cookie),
            "owner_name": app.state.db.setting("owner_name") or "",
            "username": user["username"] if user else "",
            "display_name": user["display_name"] if user else "",
            "role": role,
            "users_file": str(app.state.users.path) if role == "admin" else "",
        }

    @app.get("/api/me")
    def me(request: Request):
        return session_info(request)

    @app.post("/api/setup")
    def first_run(request: Request, body: dict):
        require_local(request)
        if app.state.users.users:
            raise HTTPException(status_code=400, detail="An admin user is already set in users.env.")
        pin_value = str(body.get("pin") or "")
        confirm = str(body.get("confirm") or pin_value)
        if pin_value != confirm:
            raise HTTPException(status_code=400, detail="The two PINs do not match.")
        if not pin_value.isdigit() or not 4 <= len(pin_value) <= 8:
            raise HTTPException(status_code=400, detail="Use a PIN of 4 to 8 digits.")
        if pin_too_easy(pin_value):
            raise HTTPException(
                status_code=400,
                detail="That PIN is too easy. Do not use 0000, 1234, or a repeated digit.",
            )
        name = str(body.get("owner_name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Type your name.")
        email = str(body.get("email") or "").strip()
        mobile = normalize_mobile(body.get("mobile"))
        if not email_ok(email):
            raise HTTPException(
                status_code=400,
                detail="Enter the email used for the bill check, like name@example.com.",
            )
        if not mobile_ok(mobile):
            raise HTTPException(
                status_code=400,
                detail="Enter a 10-digit mobile. Tata Power's form asks for it for the e-receipt.",
            )
        try:
            account = app.state.users.write_first_admin(pin_value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        user = account.public()
        app.state.db.set_setting("owner_name", name)
        app.state.db.set_setting("receipt_email", email)
        app.state.db.set_setting("receipt_mobile", mobile)
        remember(request, user)
        return session_info(request)

    @app.get("/api/ready")
    def ready(_: dict = Depends(signed_in)):
        return doctor_report()

    @app.get("/api/activity")
    def activity(_: dict = Depends(admin_only)):
        return {"activity": app.state.db.list_activity()}

    @app.get("/api/dashboard")
    def dash(_: None = Depends(signed_in)):
        today = current_day()
        app.state.db.rollover(today)
        sync_alerts(app.state.db, today)
        board = dashboard(app.state.db, today)
        board["phone_url"] = phone_url(app.state.port, app.state.public_scheme)
        board["phone_access"] = app.state.phone_flag.is_file()
        board["owner_name"] = app.state.db.setting("owner_name") or ""
        return board

    @app.get("/api/alerts")
    def alerts(_: None = Depends(signed_in)):
        return {"alerts": app.state.db.list_alerts(), "unseen": app.state.db.unseen_count()}

    @app.post("/api/alerts/seen")
    def alerts_seen(_: None = Depends(signed_in)):
        app.state.db.mark_alerts_seen()
        return {"ok": True}

    @app.get("/api/notifications")
    def notifications_list(_: None = Depends(signed_in)):
        today = current_day()
        sync_alerts(app.state.db, today)
        items = build_notifications(app.state.db, today)
        return {"items": items, "unread": unread_count(items)}

    @app.post("/api/notifications/read")
    def notifications_read(body: dict, _: None = Depends(signed_in)):
        key = str(body.get("id") or "")
        if key:
            app.state.db.mark_notification_read(key)
        return {"ok": True}

    @app.post("/api/notifications/read-all")
    def notifications_read_all(_: None = Depends(signed_in)):
        items = build_notifications(app.state.db, current_day())
        app.state.db.mark_all_notifications_read([item["id"] for item in items])
        return {"ok": True}

    @app.get("/api/tenants")
    def tenants(_: None = Depends(signed_in)):
        return {"tenants": app.state.db.list_tenants()}

    @app.post("/api/tenants")
    def create_tenant(body: dict, user: dict = Depends(signed_in)):
        try:
            row = app.state.db.save_tenant(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sync_alerts(app.state.db, current_day())
        app.state.db.log_activity(user["username"], "add tenant", row.get("name") or "")
        return row

    @app.get("/api/tenants/{tenant_id}/detail")
    def tenant_detail(tenant_id: int, _: None = Depends(signed_in)):
        tenant = app.state.db.get_tenant(tenant_id)
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found.")
        today = current_day()
        cycle = month_key(today)
        rent_row = next(
            (row for row in app.state.db.rent_rows(cycle, today) if row["id"] == tenant_id),
            None,
        )
        ca = tenant.get("ca_no") or ""
        bill = app.state.db.bill_map().get(ca) if ca else None
        bill_history = [row for row in app.state.db.bill_history_rows() if row.get("ca_no") == ca]
        return {
            "tenant": tenant,
            "rent_this_month": rent_row,
            "bill": bill,
            "rent_history": app.state.db.rent_history(tenant_id),
            "bill_history": bill_history,
            "today": today.isoformat(),
        }

    @app.put("/api/tenants/{tenant_id}")
    def update_tenant(tenant_id: int, body: dict, user: dict = Depends(signed_in)):
        try:
            row = app.state.db.save_tenant(body, tenant_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Tenant not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        app.state.db.log_activity(user["username"], "edit tenant", row.get("name") or "")
        return row

    @app.delete("/api/tenants/{tenant_id}")
    def remove_tenant(tenant_id: int, user: dict = Depends(admin_only)):
        app.state.db.delete_tenant(tenant_id)
        app.state.db.log_activity(user["username"], "delete tenant", str(tenant_id))
        return {"ok": True}

    @app.get("/api/tenants/template.xlsx")
    def tenant_template(_: None = Depends(signed_in)):
        blob = build_template()
        return Response(
            blob,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=bill-rent-template.xlsx"},
        )

    @app.post("/api/tenants/import")
    async def import_tenants(
        file: UploadFile = File(...),
        on_duplicate: str = "update",
        preview: int = 0,
        _: None = Depends(signed_in),
    ):
        payload = await file.read()
        try:
            rows = read_upload(file.filename or "tenants.csv", payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if on_duplicate not in ("update", "skip"):
            raise HTTPException(status_code=400, detail="Choose update or skip for duplicates.")
        added = updated = skipped = rejected = 0
        paid = 0
        report = []
        today = current_day()
        cycle = month_key(today)
        existing = app.state.db.list_tenants()
        for row in rows:
            issues = list(row.get("issues") or [])
            label = row.get("name") or row.get("ca_no") or f"Row {row.get('row_number')}"
            duplicate = _import_duplicate(existing, row)
            if issues:
                rejected += 1
                report.append({"name": label, "ca_no": row.get("ca_no") or "", "action": "rejected", "issues": issues})
                continue
            if duplicate and on_duplicate == "skip":
                skipped += 1
                report.append({"name": label, "ca_no": row.get("ca_no") or "", "action": "skipped", "issues": []})
                continue
            action_name = "updated" if duplicate else "added"
            if preview:
                report.append({"name": label, "ca_no": row.get("ca_no") or "", "action": action_name, "issues": []})
                if duplicate:
                    updated += 1
                else:
                    added += 1
                continue
            mark = row.pop("mark_paid", False)
            row.pop("issues", None)
            row.pop("row_number", None)
            action = app.state.db.upsert_imported(row)
            if action == "added":
                added += 1
            else:
                updated += 1
            report.append({"name": label, "ca_no": row.get("ca_no") or "", "action": action, "issues": []})
            if mark and row.get("ca_no"):
                match = next((item for item in app.state.db.list_tenants() if item["ca_no"] == row["ca_no"]), None)
                if match:
                    app.state.db.mark_rent(match["id"], cycle, "paid", match["rent"], today.isoformat(), "", "Imported as paid")
                    paid += 1
            existing = app.state.db.list_tenants()
        accepted = added + updated
        return {
            "ok": True,
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "rejected": rejected,
            "marked_paid": paid,
            "rows": accepted,
            "preview": bool(preview),
            "report": report,
        }

    @app.get("/api/export.xlsx")
    def export(_: None = Depends(signed_in)):
        blob = build_workbook(app.state.db, current_day())
        return Response(
            blob,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": "attachment; filename=bill-rent-manager.xlsx"},
        )

    @app.get("/api/rent")
    def rent(cycle: str = "", _: None = Depends(signed_in)):
        today = current_day()
        app.state.db.rollover(today)
        chosen = cycle or month_key(today)
        rows = app.state.db.rent_rows(chosen, today)
        collected = sum((row["paid_amount"] or row["rent"] or 0) for row in rows if row["paid"])
        pending = sum((row["rent"] or 0) for row in rows if not row["paid"])
        return {
            "cycle": chosen,
            "today": today.isoformat(),
            "rows": rows,
            "collected": collected,
            "pending": pending,
            "paid_count": sum(1 for row in rows if row["paid"]),
            "open_count": sum(1 for row in rows if not row["paid"]),
        }

    @app.get("/api/rent/history")
    def rent_history(tenant_id: int, _: None = Depends(signed_in)):
        return {"entries": app.state.db.rent_history(tenant_id)}

    @app.post("/api/rent/mark")
    def mark_rent(body: dict, user: dict = Depends(signed_in)):
        today = current_day()
        cycle = body.get("cycle") or month_key(today)
        amount = body.get("amount")
        status_name = body.get("status") or "paid"
        if amount in ("", None):
            amount = None
        elif status_name == "unpaid":
            amount = parse_rent(amount)
        else:
            try:
                amount = float(amount)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Enter an amount greater than zero.") from None
            if amount <= 0:
                raise HTTPException(status_code=400, detail="Enter an amount greater than zero.")
        try:
            app.state.db.mark_rent(
                int(body["tenant_id"]),
                cycle,
                body.get("status") or "paid",
                amount,
                str(body.get("paid_on") or ""),
                str(body.get("mode") or ""),
                str(body.get("note") or ""),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Tenant not found.") from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sync_alerts(app.state.db, today)
        status = str(body.get("status") or "paid")
        app.state.db.log_activity(user["username"], "mark rent", status)
        return {"ok": True}

    @app.get("/api/bills")
    def bills(_: None = Depends(signed_in)):
        return {
            "bills": app.state.db.bill_rows(),
            "progress": app.state.service.snapshot(),
            "today": current_day().isoformat(),
            "receipt_ready": app.state.db.public_settings()["receipt_ready"],
            "demo": demo,
        }

    @app.post("/api/bills/refresh")
    def refresh(body: dict, user: dict = Depends(signed_in)):
        if app.state.live_bills:
            report = doctor_report()
            if not report["ok"]:
                raise HTTPException(status_code=400, detail=report["headline"])
        selected = body.get("selected") or []
        result = app.state.service.enqueue(body.get("mode") or "all", body.get("ca_no") or "", selected)
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=result.get("error") or "Could not queue.")
        app.state.db.log_activity(user["username"], "refresh bills", str(body.get("mode") or "all"))
        return result

    @app.post("/api/bills/cancel")
    def cancel_bills(_: None = Depends(signed_in)):
        return app.state.service.cancel()

    @app.post("/api/bills/diagnose")
    def diagnose_bill(body: dict, user: dict = Depends(signed_in)):
        ca_no = "".join(ch for ch in str(body.get("ca_no") or "") if ch.isdigit())
        if len(ca_no) != 11:
            raise HTTPException(status_code=400, detail="Enter an 11-digit CA number.")
        report = app.state.service.diagnose(ca_no)
        if not report.get("ok"):
            raise HTTPException(status_code=400, detail=report.get("error") or "Diagnose failed.")
        app.state.db.log_activity(user["username"], "diagnose bill", ca_no)
        return report

    @app.get("/api/bills/runs")
    def bill_runs(_: None = Depends(signed_in)):
        return {"runs": app.state.db.list_runs()}

    @app.get("/api/bills/progress")
    def progress(_: None = Depends(signed_in)):
        return app.state.service.snapshot()

    @app.get("/api/bills/stream")
    def stream(request: Request, _: None = Depends(signed_in)):
        box = app.state.service.subscribe()

        def events():
            try:
                while True:
                    try:
                        snap = box.get(timeout=12)
                    except queue.Empty:
                        yield ": ping\n\n"
                        continue
                    yield "data: " + json.dumps(snap) + "\n\n"
            finally:
                app.state.service.unsubscribe(box)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/api/settings")
    def get_settings(_: None = Depends(signed_in)):
        data = app.state.db.public_settings()
        data["has_pin"] = bool(app.state.users.users)
        data["phone_access"] = app.state.phone_flag.is_file()
        return data

    @app.put("/api/settings")
    def put_settings(body: dict, user: dict = Depends(admin_only)):
        try:
            saved = app.state.db.update_settings(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        app.state.db.log_activity(user["username"], "edit settings", "")
        return saved

    @app.get("/api/backup.db")
    def download_backup(request: Request, _: dict = Depends(admin_only)):
        require_local(request)
        blob = app.state.db.backup_bytes()
        return Response(
            blob,
            media_type="application/octet-stream",
            headers={"Content-Disposition": "attachment; filename=bill-rent-manager.db"},
        )

    @app.post("/api/restore")
    async def restore_backup(request: Request, file: UploadFile = File(...), user: dict = Depends(admin_only)):
        require_local(request)
        payload = await file.read()
        try:
            app.state.db.restore_bytes(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        app.state.db.log_activity(user["username"], "restore backup", file.filename or "")
        return {"ok": True}

    @app.post("/api/phone-access")
    def set_phone_access(request: Request, body: dict, user: dict = Depends(admin_only)):
        require_local(request)
        enabled = body.get("enabled") in (True, "true", "1", 1, "on")
        flag = app.state.phone_flag
        if enabled:
            flag.write_text("on\n", encoding="ascii")
        elif flag.is_file():
            flag.unlink()
        app.state.db.log_activity(user["username"], "phone access", "on" if flag.is_file() else "off")
        return {
            "enabled": flag.is_file(),
            "restart": True,
            "phone_url": phone_url(app.state.port, app.state.public_scheme),
        }

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/sw.js")
    def service_worker():
        return FileResponse(STATIC / "sw.js", media_type="text/javascript")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
