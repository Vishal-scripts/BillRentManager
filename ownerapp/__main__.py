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
from ownerapp.paths import data_dir, home, lan_ip, listen_host, phone_access_enabled, phone_url
from ownerapp.tls import ensure_dev_cert
from ownerapp.users_file import UserDirectory, pin_too_easy

APP_DIR = Path(__file__).resolve().parent


def choose_port(preferred: int, host: str) -> int:
    """Use the preferred port, or the next free one. The window prints the choice."""
    for port in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
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
    parser.add_argument("--host", default="", help="Listen address. Default is this computer only.")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--phone", action="store_true", help="Also listen for phones on this Wi-Fi.")
    parser.add_argument("--https", action="store_true", help="Serve HTTPS with a certificate in the data folder.")
    args = parser.parse_args(argv)
    env = merged_env(APP_DIR)
    phone = phone_access_enabled(args.phone)
    host = listen_host(args.host, phone)
    https = args.https or (env.get("HTTPS") or "").strip().lower() in {"1", "true", "yes", "on"}
    preferred = args.port or int(env.get("PORT") or "8765")
    port = choose_port(preferred, host)
    pin = (env.get("OWNER_PIN") or "").strip()
    if pin and pin_too_easy(pin):
        print("OWNER_PIN is too easy (0000, 1234, or a repeated digit). The browser will ask for a PIN instead.")
        pin = ""
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
        secure_cookie=https,
    )
    scheme = "https" if https else "http"
    app.state.public_scheme = scheme
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
    print(f"On this PC:  {scheme}://127.0.0.1:{port}")
    if phone:
        print(f"On a phone:  {phone_url(port, scheme)}")
        print("Phone access is on. A person on this Wi-Fi who knows a username and PIN can open the app.")
    else:
        print("Phone access is off. This address works on this computer only.")
        print("To allow a phone, turn on phone access in Settings, restart, and allow the firewall.")
    if https:
        print("The phone will warn that the certificate is new. Accept it once for this computer.")
    else:
        print("System alerts on a phone need HTTPS. Start with --https if you want them. The list inside the app works on plain http.")
    print("Leave this window open. Close it to stop the app.")
    ssl_kwargs = {}
    if https:
        cert, key = ensure_dev_cert(folder / "certs", ["localhost", "127.0.0.1", lan_ip()])
        ssl_kwargs = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
    uvicorn.run(app, host=host, port=port, log_level="info", **ssl_kwargs)


if __name__ == "__main__":
    main()
