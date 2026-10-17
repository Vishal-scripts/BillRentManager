"""SQLite storage for one owner. The file stays on this PC."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ownerapp.contacts import email_ok, mobile_ok, normalize_mobile, receipt_ready
from ownerapp.rent_logic import (
    clean_phone,
    initial_closed_through,
    month_key,
    months_to_close,
    normalize_ca,
    parse_due_day,
    parse_rent,
    rent_tone,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tenants (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  room TEXT NOT NULL DEFAULT '',
  phone TEXT NOT NULL DEFAULT '',
  rent REAL,
  due_day INTEGER,
  ca_no TEXT NOT NULL DEFAULT '',
  notes TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rent_entries (
  id INTEGER PRIMARY KEY,
  tenant_id INTEGER NOT NULL,
  cycle TEXT NOT NULL,
  status TEXT NOT NULL,
  amount REAL,
  paid_on TEXT NOT NULL DEFAULT '',
  mode TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  UNIQUE(tenant_id, cycle)
);
CREATE TABLE IF NOT EXISTS bills (
  ca_no TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT '',
  amount REAL,
  due_date TEXT NOT NULL DEFAULT '',
  exact_bill REAL,
  bill_no TEXT NOT NULL DEFAULT '',
  bill_date TEXT NOT NULL DEFAULT '',
  consumer_name TEXT NOT NULL DEFAULT '',
  last_payment REAL,
  last_paid_on TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  checked_at TEXT NOT NULL DEFAULT '',
  check_state TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS bill_jobs (
  id INTEGER PRIMARY KEY,
  run_id TEXT NOT NULL DEFAULT '',
  ca_no TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  mode TEXT NOT NULL,
  state TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  requested_at TEXT NOT NULL,
  claimed_at TEXT NOT NULL DEFAULT '',
  finished_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL,
  seen INTEGER NOT NULL DEFAULT 0,
  dedupe_key TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS bill_runs (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  log_text TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  display_name TEXT NOT NULL DEFAULT '',
  pin_salt TEXT NOT NULL,
  pin_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'staff',
  active INTEGER NOT NULL DEFAULT 1,
  fails INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_reads (
  item_key TEXT PRIMARY KEY,
  read_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bill_history (
  id INTEGER PRIMARY KEY,
  ca_no TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '',
  amount REAL,
  due_date TEXT NOT NULL DEFAULT '',
  exact_bill REAL,
  bill_no TEXT NOT NULL DEFAULT '',
  bill_date TEXT NOT NULL DEFAULT '',
  consumer_name TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  checked_at TEXT NOT NULL
);
"""

MAX_USERS = 10
LOCK_AFTER = 5
LOCK_MINUTES = 5
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{1,31}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def hash_pin(pin: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return salt, digest.hex()


def pin_matches(pin: str, salt: str, digest: str) -> bool:
    _, got = hash_pin(pin, salt)
    return hmac.compare_digest(got, digest)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._migrate_legacy_pin()
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _get(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def _set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            return self._get(key, default)

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._set(key, value)
            self._conn.commit()

    def ensure_secret(self) -> str:
        with self._lock:
            current = self._get("session_secret")
            if current:
                return current
            current = secrets.token_hex(32)
            self._set("session_secret", current)
            self._conn.commit()
            return current

    def ensure_pin(self, pin_from_env: str | None) -> bool:
        """Return True when a user can sign in. The env PIN becomes user admin on first launch."""
        with self._lock:
            if self._user_count() > 0:
                return True
            pin = (pin_from_env or "").strip()
            if not pin.isdigit() or not 4 <= len(pin) <= 8:
                return False
            self._insert_user("admin", self._get("owner_name") or "Admin", pin, "admin")
            self._conn.commit()
            return True

    def check_pin(self, pin: str) -> bool:
        with self._lock:
            salt = self._get("pin_salt")
            digest = self._get("pin_hash")
        if not salt or not digest:
            return False
        return pin_matches(pin, salt, digest)

    def change_pin(self, pin: str) -> None:
        """Keep the legacy settings PIN in step with user admin, for old backups."""
        with self._lock:
            row = self._conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
            if row:
                salt, digest = hash_pin(pin)
                now = utc_now()
                self._conn.execute(
                    "UPDATE users SET pin_salt = ?, pin_hash = ?, fails = 0, locked_until = '', updated_at = ? WHERE id = ?",
                    (salt, digest, now, row["id"]),
                )
            else:
                salt, digest = hash_pin(pin)
            self._set("pin_salt", salt)
            self._set("pin_hash", digest)
            self._conn.commit()

    def _migrate_legacy_pin(self) -> None:
        if self._user_count() > 0:
            return
        digest = self._get("pin_hash")
        salt = self._get("pin_salt")
        if not digest or not salt:
            return
        name = (self._get("owner_name") or "Admin").strip() or "Admin"
        now = utc_now()
        self._conn.execute(
            """INSERT INTO users(
                 username, display_name, pin_salt, pin_hash, role, active, fails, locked_until, created_at, updated_at
               ) VALUES('admin', ?, ?, ?, 'admin', 1, 0, '', ?, ?)""",
            (name, salt, digest, now, now),
        )

    def _user_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])

    def has_users(self) -> bool:
        with self._lock:
            return self._user_count() > 0

    def _insert_user(self, username: str, display_name: str, pin: str, role: str) -> sqlite3.Row:
        salt, digest = hash_pin(pin)
        now = utc_now()
        self._conn.execute(
            """INSERT INTO users(
                 username, display_name, pin_salt, pin_hash, role, active, fails, locked_until, created_at, updated_at
               ) VALUES(?, ?, ?, ?, ?, 1, 0, '', ?, ?)""",
            (username, display_name, salt, digest, role, now, now),
        )
        if role == "admin" and username.lower() == "admin":
            self._set("pin_salt", salt)
            self._set("pin_hash", digest)
        return self._conn.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()

    def _public_user(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
            "active": bool(row["active"]),
            "locked_until": row["locked_until"] or "",
            "created_at": row["created_at"],
        }

    def _admin_count(self, exclude_id: int | None = None) -> int:
        if exclude_id is None:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND active = 1"
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND active = 1 AND id != ?",
                (exclude_id,),
            ).fetchone()
        return int(row["n"])

    def list_users(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [self._public_user(row) for row in rows]

    def user_by_id(self, user_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._public_user(row) if row else None

    def authenticate(self, username: str, pin: str) -> tuple[dict | None, str]:
        username = (username or "").strip()
        now = datetime.now(timezone.utc)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
            ).fetchone()
            if row is None:
                return None, "bad"
            if not row["active"]:
                return None, "disabled"
            locked = row["locked_until"] or ""
            fails = int(row["fails"] or 0)
            if locked:
                try:
                    until = datetime.fromisoformat(locked)
                    if until.tzinfo is None:
                        until = until.replace(tzinfo=timezone.utc)
                except ValueError:
                    until = None
                if until and until > now:
                    return None, "locked"
                fails = 0
            if not pin_matches(pin, row["pin_salt"], row["pin_hash"]):
                fails += 1
                locked_until = ""
                status = "bad"
                if fails >= LOCK_AFTER:
                    locked_until = (now + timedelta(minutes=LOCK_MINUTES)).replace(microsecond=0).isoformat()
                    status = "locked"
                self._conn.execute(
                    "UPDATE users SET fails = ?, locked_until = ?, updated_at = ? WHERE id = ?",
                    (fails, locked_until, utc_now(), row["id"]),
                )
                self._conn.commit()
                return None, status
            self._conn.execute(
                "UPDATE users SET fails = 0, locked_until = '', updated_at = ? WHERE id = ?",
                (utc_now(), row["id"]),
            )
            self._conn.commit()
            return self._public_user(row), "ok"

    def create_user(self, username: str, display_name: str, pin: str, role: str) -> dict:
        username = (username or "").strip()
        display_name = (display_name or username).strip()
        role = (role or "staff").strip().lower()
        if not USERNAME_RE.match(username):
            raise ValueError("Username must start with a letter and use 2 to 32 letters, numbers, dots, or dashes.")
        if role not in ("admin", "staff"):
            raise ValueError("Role must be Admin or Staff.")
        if not str(pin).isdigit() or not 4 <= len(str(pin)) <= 8:
            raise ValueError("Use a PIN of 4 to 8 digits.")
        with self._lock:
            if self._user_count() >= MAX_USERS:
                raise ValueError("This app holds at most 10 users.")
            taken = self._conn.execute(
                "SELECT id FROM users WHERE username = ? COLLATE NOCASE", (username,)
            ).fetchone()
            if taken:
                raise ValueError("That username is already used.")
            row = self._insert_user(username, display_name, str(pin), role)
            self._conn.commit()
            return self._public_user(row)

    def update_user(self, user_id: int, fields: dict) -> dict:
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise KeyError(user_id)
            display_name = row["display_name"]
            role = row["role"]
            active = int(row["active"])
            if "display_name" in fields:
                display_name = str(fields.get("display_name") or "").strip()
                if not display_name:
                    raise ValueError("Type a name.")
            if "role" in fields:
                role = str(fields.get("role") or "").strip().lower()
                if role not in ("admin", "staff"):
                    raise ValueError("Role must be Admin or Staff.")
            if "active" in fields:
                active = 1 if fields.get("active") else 0
            if row["role"] == "admin" and row["active"] and (role != "admin" or not active):
                if self._admin_count(exclude_id=user_id) < 1:
                    raise ValueError("Keep at least one active admin.")
            pin = fields.get("pin")
            if pin not in (None, ""):
                if not str(pin).isdigit() or not 4 <= len(str(pin)) <= 8:
                    raise ValueError("Use a PIN of 4 to 8 digits.")
                salt, digest = hash_pin(str(pin))
                self._conn.execute(
                    "UPDATE users SET pin_salt = ?, pin_hash = ?, fails = 0, locked_until = '' WHERE id = ?",
                    (salt, digest, user_id),
                )
                if row["username"].lower() == "admin":
                    self._set("pin_salt", salt)
                    self._set("pin_hash", digest)
            self._conn.execute(
                "UPDATE users SET display_name = ?, role = ?, active = ?, updated_at = ? WHERE id = ?",
                (display_name, role, active, utc_now(), user_id),
            )
            self._conn.commit()
            fresh = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return self._public_user(fresh)

    def delete_user(self, user_id: int) -> None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                raise KeyError(user_id)
            if row["role"] == "admin" and row["active"] and self._admin_count(exclude_id=user_id) < 1:
                raise ValueError("Keep at least one active admin.")
            self._conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            self._conn.commit()

    def set_own_pin(self, user_id: int, current: str, new_pin: str) -> None:
        if not str(new_pin).isdigit() or not 4 <= len(str(new_pin)) <= 8:
            raise ValueError("Use a PIN of 4 to 8 digits.")
        with self._lock:
            row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None or not pin_matches(current, row["pin_salt"], row["pin_hash"]):
                raise ValueError("Current PIN is wrong.")
            salt, digest = hash_pin(str(new_pin))
            self._conn.execute(
                "UPDATE users SET pin_salt = ?, pin_hash = ?, fails = 0, locked_until = '', updated_at = ? WHERE id = ?",
                (salt, digest, utc_now(), user_id),
            )
            if row["username"].lower() == "admin":
                self._set("pin_salt", salt)
                self._set("pin_hash", digest)
            self._conn.commit()

    def log_activity(self, username: str, action: str, detail: str = "") -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO activity(username, action, detail, created_at) VALUES(?, ?, ?, ?)",
                (username or "", action, detail or "", utc_now()),
            )
            self._conn.commit()

    def list_activity(self, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit or 100), 300))
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, username, action, detail, created_at FROM activity ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def public_settings(self) -> dict:
        with self._lock:
            return {
                "receipt_email": self._get("receipt_email", "") or "",
                "receipt_mobile": self._get("receipt_mobile", "") or "",
                "request_delay": self._get("request_delay", "3") or "3",
                "captcha_tries": self._get("captcha_tries", "8") or "8",
                "bill_check_mode": self._get("bill_check_mode", "auto") or "auto",
                "auto_refresh": (self._get("auto_refresh", "false") or "false") == "true",
                "auto_weekday": int(self._get("auto_weekday", "0") or "0"),
                "auto_time": self._get("auto_time", "09:00") or "09:00",
                "demo": (self._get("demo", "false") or "false") == "true",
                "has_pin": self._user_count() > 0,
                "receipt_ready": receipt_ready(
                    self._get("receipt_email", "") or "",
                    self._get("receipt_mobile", "") or "",
                ),
            }

    def update_settings(self, fields: dict) -> dict:
        allowed = {
            "receipt_email",
            "receipt_mobile",
            "request_delay",
            "captcha_tries",
            "bill_check_mode",
            "auto_refresh",
            "auto_weekday",
            "auto_time",
        }
        if "receipt_email" in fields and not email_ok(fields.get("receipt_email")):
            raise ValueError(
                "Enter an email like name@example.com. Tata Power's form marks it as required."
            )
        if "receipt_mobile" in fields and not mobile_ok(fields.get("receipt_mobile")):
            raise ValueError("Enter a 10-digit mobile for the e-receipt.")
        if "receipt_mobile" in fields:
            fields = dict(fields)
            fields["receipt_mobile"] = normalize_mobile(fields.get("receipt_mobile"))
        with self._lock:
            for key, value in fields.items():
                if key not in allowed:
                    continue
                if key == "auto_refresh":
                    value = "true" if value in (True, "true", "1", 1) else "false"
                if key == "captcha_tries":
                    try:
                        value = str(max(1, min(8, int(value))))
                    except (TypeError, ValueError):
                        value = "8"
                if key == "request_delay":
                    try:
                        value = str(max(2, float(value)))
                    except (TypeError, ValueError):
                        value = "3"
                if key == "bill_check_mode":
                    value = str(value or "auto").strip().lower()
                    if value not in ("auto", "fast", "browser"):
                        value = "auto"
                if key == "auto_weekday":
                    try:
                        value = str(max(0, min(6, int(value))))
                    except (TypeError, ValueError):
                        value = "0"
                self._set(key, str(value))
            self._conn.commit()
        return self.public_settings()

    def apply_env_defaults(self, env: dict) -> None:
        mapping = {
            "RECEIPT_EMAIL": "receipt_email",
            "RECEIPT_MOBILE": "receipt_mobile",
            "REQUEST_DELAY_SECONDS": "request_delay",
            "NDPL_CAPTCHA_TRIES": "captcha_tries",
        }
        with self._lock:
            for env_key, setting in mapping.items():
                if self._get(setting):
                    continue
                raw = (env.get(env_key) or "").strip()
                if raw:
                    self._set(setting, raw)
            self._conn.commit()

    def rollover(self, today: date) -> list[str]:
        """Freeze finished months. Returns the month keys that were closed."""
        with self._lock:
            closed = self._get("closed_through")
            if not closed:
                self._set("closed_through", initial_closed_through(today))
                self._conn.commit()
                return []
            months = months_to_close(today, closed)
            if not months:
                return []
            tenants = self._conn.execute(
                "SELECT id, rent FROM tenants WHERE active = 1 AND rent IS NOT NULL"
            ).fetchall()
            now = utc_now()
            for cycle in months:
                for tenant in tenants:
                    self._conn.execute(
                        """
                        INSERT INTO rent_entries(tenant_id, cycle, status, amount, paid_on, mode, note, updated_at)
                        VALUES(?, ?, 'unpaid', ?, '', '', 'Closed when the month changed', ?)
                        ON CONFLICT(tenant_id, cycle) DO NOTHING
                        """,
                        (tenant["id"], cycle, tenant["rent"], now),
                    )
                self._set("closed_through", cycle)
            self._conn.commit()
            return months

    def list_tenants(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM tenants ORDER BY name COLLATE NOCASE, id").fetchall()
        return [dict(row) for row in rows]

    def get_tenant(self, tenant_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
        return dict(row) if row else None

    def save_tenant(self, fields: dict, tenant_id: int | None = None) -> dict:
        name = str(fields.get("name") or "").strip()
        room = str(fields.get("room") or "").strip()
        phone = clean_phone(fields.get("phone"))
        rent = parse_rent(fields.get("rent"))
        due_day = parse_due_day(fields.get("due_day"))
        ca = normalize_ca(fields.get("ca_no")) or ""
        if fields.get("ca_no") and not ca:
            raise ValueError("CA number must be 11 digits, or leave it blank.")
        notes = str(fields.get("notes") or "").strip()
        active = 0 if fields.get("active") in (False, 0, "0", "false") else 1
        now = utc_now()
        with self._lock:
            if tenant_id:
                existing = self._conn.execute("SELECT id FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
                if not existing:
                    raise KeyError("tenant")
                self._conn.execute(
                    """
                    UPDATE tenants
                    SET name=?, room=?, phone=?, rent=?, due_day=?, ca_no=?, notes=?, active=?, updated_at=?
                    WHERE id=?
                    """,
                    (name, room, phone, rent, due_day, ca, notes, active, now, tenant_id),
                )
            else:
                cur = self._conn.execute(
                    """
                    INSERT INTO tenants(name, room, phone, rent, due_day, ca_no, notes, active, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (name, room, phone, rent, due_day, ca, notes, active, now, now),
                )
                tenant_id = int(cur.lastrowid)
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
        return dict(row)

    def delete_tenant(self, tenant_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM rent_entries WHERE tenant_id = ?", (tenant_id,))
            self._conn.execute("DELETE FROM tenants WHERE id = ?", (tenant_id,))
            self._conn.commit()

    def upsert_imported(self, row: dict) -> str:
        """Insert or update one imported tenant. Returns added or updated."""
        ca = normalize_ca(row.get("ca_no")) or ""
        name = str(row.get("name") or "").strip()
        room = str(row.get("room") or "").strip()
        with self._lock:
            existing = None
            if ca:
                existing = self._conn.execute("SELECT * FROM tenants WHERE ca_no = ?", (ca,)).fetchone()
            if existing is None and name:
                existing = self._conn.execute(
                    "SELECT * FROM tenants WHERE lower(name) = lower(?) AND lower(room) = lower(?)",
                    (name, room),
                ).fetchone()
            if existing:
                tenant_id = existing["id"]
                action = "updated"
            else:
                tenant_id = None
                action = "added"
        payload = {
            "name": name or (existing["name"] if existing else ""),
            "room": room or (existing["room"] if existing else ""),
            "phone": row.get("phone") if row.get("phone") not in (None, "") else (existing["phone"] if existing else ""),
            "rent": row.get("rent") if row.get("rent") not in (None, "") else (existing["rent"] if existing else None),
            "due_day": row.get("due_day") if row.get("due_day") not in (None, "") else (existing["due_day"] if existing else None),
            "ca_no": ca or (existing["ca_no"] if existing else ""),
            "notes": row.get("notes") if row.get("notes") is not None else (existing["notes"] if existing else ""),
            "active": True,
        }
        self.save_tenant(payload, tenant_id)
        return action

    def rent_rows(self, cycle: str, today: date) -> list[dict]:
        with self._lock:
            tenants = self._conn.execute(
                "SELECT * FROM tenants WHERE active = 1 ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
            entries = {
                row["tenant_id"]: dict(row)
                for row in self._conn.execute("SELECT * FROM rent_entries WHERE cycle = ?", (cycle,)).fetchall()
            }
        out = []
        for tenant in tenants:
            item = dict(tenant)
            entry = entries.get(item["id"])
            paid = bool(entry and entry["status"] == "paid")
            tone = rent_tone(today, item["due_day"], paid, cycle)
            item["cycle"] = cycle
            item["paid"] = paid
            item["tone"] = tone.tone
            item["tone_label"] = tone.label
            item["due_date"] = tone.due.isoformat() if tone.due else ""
            item["paid_amount"] = entry["amount"] if entry else None
            item["paid_on"] = entry["paid_on"] if entry else ""
            item["paid_mode"] = entry["mode"] if entry else ""
            item["entry_note"] = entry["note"] if entry else ""
            out.append(item)
        return out

    def mark_rent(self, tenant_id: int, cycle: str, status: str, amount, paid_on: str, mode: str, note: str) -> None:
        if status not in ("paid", "unpaid"):
            raise ValueError("Status must be paid or unpaid.")
        if not cycle or len(cycle) != 7:
            raise ValueError("Choose a month.")
        now = utc_now()
        with self._lock:
            tenant = self._conn.execute("SELECT id FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
            if not tenant:
                raise KeyError("tenant")
            self._conn.execute(
                """
                INSERT INTO rent_entries(tenant_id, cycle, status, amount, paid_on, mode, note, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, cycle) DO UPDATE SET
                  status=excluded.status, amount=excluded.amount, paid_on=excluded.paid_on,
                  mode=excluded.mode, note=excluded.note, updated_at=excluded.updated_at
                """,
                (tenant_id, cycle, status, amount, paid_on or "", mode or "", note or "", now),
            )
            self._conn.commit()

    def bill_map(self) -> dict[str, dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM bills").fetchall()
        return {row["ca_no"]: dict(row) for row in rows}

    def bill_rows(self) -> list[dict]:
        bills = self.bill_map()
        out = []
        for tenant in self.list_tenants():
            if not tenant["active"] or not tenant["ca_no"]:
                continue
            bill = bills.get(tenant["ca_no"], {})
            out.append(
                {
                    "tenant_id": tenant["id"],
                    "name": tenant["name"],
                    "room": tenant["room"],
                    "ca_no": tenant["ca_no"],
                    "active": bool(tenant["active"]),
                    "status": bill.get("status") or "",
                    "amount": bill.get("amount"),
                    "due_date": bill.get("due_date") or "",
                    "exact_bill": bill.get("exact_bill"),
                    "bill_no": bill.get("bill_no") or "",
                    "bill_date": bill.get("bill_date") or "",
                    "consumer_name": bill.get("consumer_name") or "",
                    "last_payment": bill.get("last_payment"),
                    "last_paid_on": bill.get("last_paid_on") or "",
                    "note": bill.get("note") or "",
                    "checked_at": bill.get("checked_at") or "",
                    "check_state": bill.get("check_state") or "",
                }
            )
        return out

    def save_bill(self, merged: dict) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO bills(ca_no, status, amount, due_date, exact_bill, bill_no, bill_date,
                  consumer_name, last_payment, last_paid_on, note, checked_at, check_state)
                VALUES(:ca_no, :status, :amount, :due_date, :exact_bill, :bill_no, :bill_date,
                  :consumer_name, :last_payment, :last_paid_on, :note, :checked_at, :check_state)
                ON CONFLICT(ca_no) DO UPDATE SET
                  status=excluded.status, amount=excluded.amount, due_date=excluded.due_date,
                  exact_bill=excluded.exact_bill, bill_no=excluded.bill_no, bill_date=excluded.bill_date,
                  consumer_name=excluded.consumer_name, last_payment=excluded.last_payment,
                  last_paid_on=excluded.last_paid_on, note=excluded.note,
                  checked_at=excluded.checked_at, check_state=excluded.check_state
                """,
                {
                    "ca_no": merged["ca_no"],
                    "status": merged.get("status") or "",
                    "amount": merged.get("amount"),
                    "due_date": merged.get("due_date") or "",
                    "exact_bill": merged.get("exact_bill"),
                    "bill_no": merged.get("bill_no") or "",
                    "bill_date": merged.get("bill_date") or "",
                    "consumer_name": merged.get("consumer_name") or "",
                    "last_payment": merged.get("last_payment"),
                    "last_paid_on": merged.get("last_paid_on") or "",
                    "note": merged.get("note") or "",
                    "checked_at": merged.get("checked_at") or "",
                    "check_state": merged.get("check_state") or "",
                },
            )
            if merged.get("checked_at"):
                self._conn.execute(
                    """
                    INSERT INTO bill_history(
                      ca_no, status, amount, due_date, exact_bill, bill_no, bill_date,
                      consumer_name, note, checked_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        merged["ca_no"],
                        merged.get("status") or "",
                        merged.get("amount"),
                        merged.get("due_date") or "",
                        merged.get("exact_bill"),
                        merged.get("bill_no") or "",
                        merged.get("bill_date") or "",
                        merged.get("consumer_name") or "",
                        merged.get("note") or "",
                        merged.get("checked_at") or "",
                    ),
                )
            self._conn.commit()

    def set_check_states(self, cas: list[str], state: str) -> None:
        if not cas:
            return
        with self._lock:
            for ca in cas:
                self._conn.execute(
                    """
                    INSERT INTO bills(ca_no, check_state) VALUES(?, ?)
                    ON CONFLICT(ca_no) DO UPDATE SET check_state=excluded.check_state
                    """,
                    (ca, state),
                )
            self._conn.commit()

    def open_job_cas(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT ca_no FROM bill_jobs WHERE state IN ('Queued', 'Checking')"
            ).fetchall()
        return [row["ca_no"] for row in rows]

    def enqueue_jobs(self, rows: list[dict], mode: str) -> None:
        now = utc_now()
        with self._lock:
            for row in rows:
                self._conn.execute(
                    """
                    INSERT INTO bill_jobs(ca_no, name, mode, state, requested_at)
                    VALUES(?, ?, ?, 'Queued', ?)
                    """,
                    (row["ca_no"], row.get("name") or "", mode, now),
                )
                self._conn.execute(
                    """
                    INSERT INTO bills(ca_no, check_state) VALUES(?, 'Queued')
                    ON CONFLICT(ca_no) DO UPDATE SET check_state='Queued'
                    """,
                    (row["ca_no"],),
                )
            self._conn.commit()

    def claim_queued(self, run_id: str) -> list[dict]:
        now = utc_now()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM bill_jobs WHERE state = 'Queued' ORDER BY id"
            ).fetchall()
            claimed = []
            for row in rows:
                self._conn.execute(
                    "UPDATE bill_jobs SET state='Checking', run_id=?, claimed_at=? WHERE id=?",
                    (run_id, now, row["id"]),
                )
                self._conn.execute(
                    """
                    INSERT INTO bills(ca_no, check_state) VALUES(?, 'Checking')
                    ON CONFLICT(ca_no) DO UPDATE SET check_state='Checking'
                    """,
                    (row["ca_no"],),
                )
                item = dict(row)
                item["state"] = "Checking"
                item["run_id"] = run_id
                claimed.append(item)
            self._conn.commit()
        return claimed

    def finish_job(self, job_id: int, state: str, note: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE bill_jobs SET state=?, note=?, finished_at=? WHERE id=?",
                (state, note[:300], utc_now(), job_id),
            )
            self._conn.commit()

    def requeue_checking(self) -> int:
        with self._lock:
            rows = self._conn.execute("SELECT id, ca_no FROM bill_jobs WHERE state = 'Checking'").fetchall()
            for row in rows:
                self._conn.execute(
                    "UPDATE bill_jobs SET state='Queued', run_id='', claimed_at='' WHERE id=?",
                    (row["id"],),
                )
                self._conn.execute(
                    "UPDATE bills SET check_state='Queued' WHERE ca_no=?",
                    (row["ca_no"],),
                )
            self._conn.commit()
        return len(rows)

    def add_alert(self, kind: str, title: str, body: str, dedupe_key: str) -> bool:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO alerts(kind, title, body, created_at, dedupe_key) VALUES(?, ?, ?, ?, ?)",
                    (kind, title, body, utc_now(), dedupe_key),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def list_alerts(self, limit: int = 40) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM alerts ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def unseen_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM alerts WHERE seen = 0").fetchone()
        return int(row["n"])

    def mark_alerts_seen(self) -> None:
        with self._lock:
            self._conn.execute("UPDATE alerts SET seen = 1 WHERE seen = 0")
            self._conn.commit()

    def notification_reads(self) -> set[str]:
        with self._lock:
            rows = self._conn.execute("SELECT item_key FROM notification_reads").fetchall()
        return {row["item_key"] for row in rows}

    def mark_notification_read(self, item_key: str) -> None:
        key = (item_key or "").strip()
        if not key:
            return
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO notification_reads(item_key, read_at) VALUES(?, ?)
                ON CONFLICT(item_key) DO UPDATE SET read_at=excluded.read_at
                """,
                (key, utc_now()),
            )
            self._conn.commit()

    def mark_all_notifications_read(self, keys: list[str]) -> None:
        if not keys:
            return
        now = utc_now()
        with self._lock:
            for key in keys:
                if not key:
                    continue
                self._conn.execute(
                    """
                    INSERT INTO notification_reads(item_key, read_at) VALUES(?, ?)
                    ON CONFLICT(item_key) DO UPDATE SET read_at=excluded.read_at
                    """,
                    (key, now),
                )
            self._conn.commit()

    def bill_history_rows(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT h.*, t.name, t.room
                FROM bill_history h
                LEFT JOIN tenants t ON t.ca_no = h.ca_no
                ORDER BY h.checked_at DESC, h.id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_alert_id(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(MAX(id), 0) AS n FROM alerts").fetchone()
        return int(row["n"])

    def rent_history(self, tenant_id: int) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT cycle, status, amount, paid_on, mode, note
                FROM rent_entries WHERE tenant_id = ?
                ORDER BY cycle DESC
                """,
                (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_run(self, started_at: str, finished_at: str, summary: str, log_text: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO bill_runs(started_at, finished_at, summary, log_text) VALUES(?, ?, ?, ?)",
                (started_at, finished_at, summary, log_text),
            )
            self._conn.commit()

    def list_runs(self, limit: int = 12) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, started_at, finished_at, summary, log_text FROM bill_runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def backup_bytes(self) -> bytes:
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(FULL)")
            return self.path.read_bytes()

    def restore_bytes(self, payload: bytes) -> None:
        if not payload.startswith(b"SQLite format 3"):
            raise ValueError("That file is not a Bill Rent Manager backup.")
        staged = self.path.with_suffix(".restore")
        staged.write_bytes(payload)
        probe = sqlite3.connect(staged)
        try:
            row = probe.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tenants'"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            staged.unlink(missing_ok=True)
            raise ValueError("That file could not be read as a database.") from exc
        finally:
            probe.close()
        if not row:
            staged.unlink(missing_ok=True)
            raise ValueError("That backup has no tenant list.")
        with self._lock:
            self._conn.close()
            staged.replace(self.path)
            self._conn = sqlite3.connect(self.path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.executescript(SCHEMA)
            self._migrate_legacy_pin()
            self._conn.commit()

    def rent_entries_for_export(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT e.*, t.name, t.room, t.ca_no
                FROM rent_entries e JOIN tenants t ON t.id = e.tenant_id
                ORDER BY e.cycle DESC, t.name COLLATE NOCASE
                """
            ).fetchall()
        return [dict(row) for row in rows]


def load_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    found = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        found[key.strip()] = value.strip()
    return found


def merged_env(app_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(load_env_file(app_dir / ".env"))
    return env
