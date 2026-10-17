"""Start the local app on the LAN. Nothing here contacts Google or Meta."""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

import uvicorn

from ownerapp.app import create_app
from ownerapp.db import merged_env
from ownerapp.doctor import run_checks
from ownerapp.paths import data_dir, home, phone_url
from ownerapp.users_file import UserDirectory

APP_DIR = Path(__file__).resolve().parent


def choose_port(preferred: int) -> int:
    """Use the preferred port, or the next free one. The window prints the choice."""
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))
            except OSError:
                continue
            return port
    raise SystemExit(f"No free port from {preferred} to {preferred + 19}. Close the other program and start again.")


def _needs_admin_pin(path: Path) -> bool:
    if not path.is_file():
        return True
    directory = UserDirectory(path)
    directory.reload(force=True)
    return not directory.users


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Bill Rent Manager, on this PC.")
    parser.add_argument("--demo", action="store_true", help="Practice bills. Does not open Tata Power.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    env = merged_env(APP_DIR)
    preferred = args.port or int(env.get("PORT") or "8765")
    port = choose_port(preferred)
    pin = (env.get("OWNER_PIN") or "").strip()
    users_path = home() / "users.env"
    if (not pin.isdigit() or not 4 <= len(pin) <= 8) and _needs_admin_pin(users_path) and sys.stdin.isatty():
        print(f"No users in {users_path}.")
        print("Type a PIN of 4 to 8 digits for the admin user. Add other people later in that file.")
        typed = input("Admin PIN: ").strip()
        if typed.isdigit() and 4 <= len(typed) <= 8:
            pin = typed
    folder = data_dir()
    db_path = folder / ("owner-demo.db" if args.demo else "owner.db")
    app = create_app(
        db_path,
        pin=pin or None,
        demo=args.demo,
        gap=None if not args.demo else 0.0,
        log_path=folder / "logs" / "bills.jsonl",
        port=port,
        users_path=users_path,
    )
    app.state.db.apply_env_defaults(env)
    report = run_checks(db_path)
    (folder / "port.txt").write_text(str(port), encoding="ascii")
    print("Bill Rent Manager")
    print("This window does not pay a bill, and it does not send WhatsApp.")
    if args.demo:
        print("Practice mode: bill numbers are fake.")
    if port != preferred:
        print(f"Port {preferred} was busy. This window is using {port}.")
    print(f"Users file: {users_path}")
    if not app.state.users.users:
        print("First run: the browser will ask for your name and a PIN. That PIN is saved as user admin.")
    else:
        print("To add or remove a person, edit users.env in Notepad, save, and restart this window.")
    if not report["ok"]:
        print("Setup problem:", report["headline"])
        for item in report["checks"]:
            if not item["ok"]:
                print(f"  {item['name']}: {item['detail']}")
                if item["fix"]:
                    print(f"  Fix: {item['fix']}")
    else:
        print("Self-check passed. Tesseract, libraries, and the data folder are ready.")
    print(f"On this PC:  http://127.0.0.1:{port}")
    print(f"On a phone:  {phone_url(port)}")
    print("Leave this window open. Close it to stop the app.")
    uvicorn.run(app, host=args.host, port=port, log_level="info")


if __name__ == "__main__":
    main()
