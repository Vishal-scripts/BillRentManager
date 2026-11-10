"""Accounts live in users.env, which the owner edits by hand.

The app never creates extra users from the browser. The first run can write
the initial admin line from the PIN chosen on the setup page. A PIN typed
in the file is hashed on the next load. The hash is useless without
pin.pepper, which sits next to users.env and is not part of a backup.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

MAX_USERS = 10
LOCK_AFTER = 5
LOCK_SECONDS = 5 * 60
PIN_ITERATIONS = 120_000
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{1,31}$")
EASY_PINS = {"0000", "1111", "1234", "123456", "654321", "1212", "0123"}

HEADER = """# username=PIN   (4 to 8 digits). Max 10 users. Save the file, then restart the app.
# You can type a PIN here. The app replaces it with a hash and does not keep the digits.
# Do not use 0000, 1234, 123456, or a single repeated digit.
# Optional role at the end: username=PIN:admin or username=PIN:staff
# The first user is an admin. Later users are staff unless the line says :admin.
# Open this file in Notepad. Add or delete lines, save, then restart the app.
"""


@dataclass(frozen=True)
class Account:
    username: str
    pin: str
    role: str

    def public(self) -> dict:
        return {"id": self.username, "username": self.username, "display_name": self.username, "role": self.role, "active": True}


def pin_too_easy(pin: str) -> bool:
    """True for PINs that are obvious if someone on the Wi-Fi guesses."""
    text = str(pin or "")
    if text in EASY_PINS:
        return True
    return bool(text) and len(set(text)) == 1


def hash_pin(pin: str, pepper: bytes, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(pin).encode("utf-8"),
        pepper + salt,
        PIN_ITERATIONS,
    ).hex()
    return f"pbkdf2${salt.hex()}${digest}"


def pins_match(got: str, stored: str, pepper: bytes | None = None) -> bool:
    """Compare a typed PIN with a stored hash, or with a legacy plaintext PIN."""
    stored = str(stored or "")
    if stored.startswith("pbkdf2$") and pepper:
        try:
            _scheme, salt_hex, digest = stored.split("$", 2)
            salt = bytes.fromhex(salt_hex)
        except ValueError:
            return False
        calc = hashlib.pbkdf2_hmac(
            "sha256",
            str(got or "").encode("utf-8"),
            pepper + salt,
            PIN_ITERATIONS,
        ).hex()
        return hmac.compare_digest(calc, digest)
    left = hashlib.sha256(str(got or "").encode("utf-8")).digest()
    right = hashlib.sha256(stored.encode("utf-8")).digest()
    return hmac.compare_digest(left, right)


def parse_users(text: str) -> tuple[dict[str, Account], list[str]]:
    """Return up to 10 accounts and human-readable warnings. Order follows the file."""
    users: dict[str, Account] = {}
    warnings: list[str] = []
    valid_seen = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        account, problem = _parse_line(line)
        if problem:
            warnings.append(f"users.env line {number}: {problem} Skipped.")
            continue
        assert account is not None
        valid_seen += 1
        if len(users) >= MAX_USERS:
            warnings.append(
                f"users.env line {number}: ignored {account.username}. This app keeps only {MAX_USERS} users."
            )
            continue
        key = account.username.lower()
        if key in users:
            warnings.append(f"users.env line {number}: {account.username} is already listed. Skipped.")
            continue
        role = account.role or ("admin" if not users else "staff")
        users[key] = Account(account.username, account.pin, role)
    if users and not any(item.role == "admin" for item in users.values()):
        first = next(iter(users.values()))
        users[first.username.lower()] = Account(first.username, first.pin, "admin")
        warnings.append(f"users.env: no admin was set. {first.username} is the admin.")
    if valid_seen == 0 and text.strip():
        warnings.append("users.env has no valid users. Sign-in stays closed until one line is valid.")
    return users, warnings


def _parse_line(line: str) -> tuple[Account | None, str]:
    role = None
    if "=" not in line:
        return None, "A line must look like username=PIN."
    username, rest = line.split("=", 1)
    username = username.strip()
    rest = rest.strip()
    if ":" in username:
        username, role = username.split(":", 1)
        username = username.strip()
        role = role.strip().lower()
    elif ":" in rest:
        pin_part, role = rest.rsplit(":", 1)
        rest = pin_part.strip()
        role = role.strip().lower()
    if not USERNAME_RE.match(username):
        return None, f"'{username}' is not a valid username."
    if rest.startswith("pbkdf2$"):
        parts = rest.split("$")
        if len(parts) != 3 or not parts[1] or not parts[2]:
            return None, "The saved PIN hash is not valid."
    elif not rest.isdigit() or not 4 <= len(rest) <= 8:
        return None, "The PIN must be 4 to 8 digits."
    if role is not None and role not in ("admin", "staff"):
        return None, "The role must be admin or staff."
    return Account(username, rest, role or ""), ""


class UserDirectory:
    def __init__(self, path: Path, clock=None) -> None:
        self.path = Path(path)
        self.pepper_path = self.path.with_name("pin.pepper")
        self.lock_path = self.path.with_name("login-lock.json")
        self.clock = clock or time.time
        self.users: dict[str, Account] = {}
        self.warnings: list[str] = []
        self.fails: dict[str, tuple[int, float]] = {}
        self._mtime: float | None = None
        self._checked = 0.0
        self._lock = threading.Lock()
        self._load_locks()

    def ensure(self, pin: str | None = None) -> None:
        """Create users.env when it is missing. A valid PIN becomes the admin line."""
        pin = (pin or "").strip()
        valid = pin.isdigit() and 4 <= len(pin) <= 8 and not pin_too_easy(pin)
        if not self.path.is_file():
            self.write_template(pin if valid else None)
            return
        self.reload(force=True)

    def write_template(self, admin_pin: str | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = HEADER
        if admin_pin:
            stored = hash_pin(admin_pin, self._load_pepper())
            body += f"admin={stored}:admin\n"
        self.path.write_text(body, encoding="utf-8")
        print(f"users.env: wrote {self.path}", flush=True)
        self.reload(force=True)

    def write_first_admin(self, pin: str) -> Account:
        """Used only for the empty first-run file. Does not add a second user."""
        if not str(pin).isdigit() or not 4 <= len(str(pin)) <= 8:
            raise ValueError("Use a PIN of 4 to 8 digits.")
        if pin_too_easy(pin):
            raise ValueError("That PIN is too easy. Do not use 0000, 1234, or a repeated digit.")
        self.reload(force=True)
        if self.users:
            raise ValueError("An admin user is already set in users.env.")
        stored = hash_pin(pin, self._load_pepper())
        text = self.path.read_text(encoding="utf-8") if self.path.is_file() else HEADER
        if not text.endswith("\n"):
            text += "\n"
        text += f"admin={stored}:admin\n"
        self.path.write_text(text, encoding="utf-8")
        self.reload(force=True)
        account = self.users.get("admin")
        if account is None:
            raise ValueError("Could not save the admin line in users.env.")
        return account

    def reload(self, force: bool = False) -> None:
        now = self.clock()
        try:
            mtime = self.path.stat().st_mtime if self.path.is_file() else None
        except OSError:
            mtime = None
        with self._lock:
            if not force and mtime == self._mtime and now - self._checked < 60:
                return
            self._checked = now
            changed = force or mtime != self._mtime
            self._mtime = mtime
            if mtime is None:
                users: dict[str, Account] = {}
                warnings = [f"users.env is missing at {self.path}. Sign-in stays closed until the file exists."]
            else:
                try:
                    text = self.path.read_text(encoding="utf-8-sig")
                except OSError as exc:
                    users = {}
                    warnings = [f"users.env could not be read: {exc}"]
                else:
                    users, warnings = parse_users(text)
                    users, hashed = self._hash_plaintext(users)
                    if hashed:
                        self._write_accounts(users)
                        try:
                            self._mtime = self.path.stat().st_mtime
                        except OSError:
                            pass
                        warnings.append("users.env: a typed PIN was replaced with a hash.")
            if changed or warnings != self.warnings:
                for line in warnings:
                    print(line, flush=True)
            self.users = users
            self.warnings = warnings

    def _hash_plaintext(self, users: dict[str, Account]) -> tuple[dict[str, Account], bool]:
        if not any(not account.pin.startswith("pbkdf2$") for account in users.values()):
            return users, False
        pepper = self._load_pepper()
        migrated: dict[str, Account] = {}
        for key, account in users.items():
            stored = account.pin if account.pin.startswith("pbkdf2$") else hash_pin(account.pin, pepper)
            migrated[key] = Account(account.username, stored, account.role)
        return migrated, True

    def _write_accounts(self, users: dict[str, Account]) -> None:
        lines = [HEADER.rstrip("\n")]
        for account in users.values():
            lines.append(f"{account.username}={account.pin}:{account.role}")
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _load_pepper(self) -> bytes:
        if self.pepper_path.is_file():
            raw = self.pepper_path.read_text(encoding="utf-8").strip()
            try:
                pepper = bytes.fromhex(raw)
            except ValueError:
                pepper = b""
            if len(pepper) >= 16:
                return pepper
        pepper = secrets.token_bytes(32)
        self.pepper_path.write_text(pepper.hex() + "\n", encoding="utf-8")
        try:
            self.pepper_path.chmod(0o600)
        except OSError:
            pass
        return pepper

    def _load_locks(self) -> None:
        self.fails = {}
        if not self.lock_path.is_file():
            return
        try:
            data = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        for key, item in data.items():
            if not isinstance(item, dict):
                continue
            self.fails[str(key)] = (int(item.get("fails") or 0), float(item.get("until") or 0))

    def _save_locks(self) -> None:
        payload = {
            key: {"fails": fails, "until": until}
            for key, (fails, until) in self.fails.items()
        }
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            self.lock_path.chmod(0o600)
        except OSError:
            pass

    def get(self, username: str) -> dict | None:
        self.reload()
        account = self.users.get((username or "").strip().lower())
        return account.public() if account else None

    def authenticate(self, username: str, pin: str) -> tuple[dict | None, str]:
        self.reload()
        key = (username or "").strip().lower()
        account = self.users.get(key)
        pepper = self._load_pepper()
        expected = account.pin if account else hash_pin("0000", pepper)
        now = float(self.clock())
        fails, until = self.fails.get(key, (0, 0.0))
        if until > now:
            pins_match(pin, expected, pepper)
            return None, "locked"
        if until and until <= now:
            fails = 0
        if account is None or not pins_match(pin, expected, pepper):
            fails += 1
            if fails >= LOCK_AFTER:
                self.fails[key] = (fails, now + LOCK_SECONDS)
                self._save_locks()
                return None, "locked"
            self.fails[key] = (fails, 0.0)
            self._save_locks()
            return None, "bad"
        self.fails.pop(key, None)
        self._save_locks()
        return account.public(), "ok"
