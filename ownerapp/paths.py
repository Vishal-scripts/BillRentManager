"""Where this copy of the app keeps its database.

The source checkout uses the repo folder. The portable Windows folder sets
NARELA_HOME to the folder that contains NarelaRentApp.exe, so the database
and logs sit next to that program and can be copied with it.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path


def home() -> Path:
    raw = os.environ.get("NARELA_HOME", "").strip().strip('"')
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    path = home() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def phone_url(port: int) -> str:
    return f"http://{lan_ip()}:{port}"
