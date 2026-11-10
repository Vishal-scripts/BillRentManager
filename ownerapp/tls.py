"""Optional self-signed HTTPS so a phone can use a secure address.

Plain http:// on a LAN IP is not a secure page. Browsers then refuse system
alerts and a service worker. Localhost is already a secure page, so the
computer itself does not need this.

The certificate is written under the data folder. It is not a public
certificate. The phone shows a warning until the owner accepts it.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def ensure_dev_cert(folder: Path, hosts: list[str]) -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    cert = folder / "cert.pem"
    key = folder / "key.pem"
    if cert.is_file() and key.is_file():
        return cert, key
    openssl = shutil.which("openssl")
    if not openssl:
        raise SystemExit(
            "HTTPS needs openssl, or put cert.pem and key.pem in the data folder. "
            "On Windows, install Git or OpenSSL and start again. "
            "Without HTTPS the app still works on http://127.0.0.1, and a phone "
            "can open the app, but the phone browser will not show system alerts."
        )
    names = []
    for host in hosts:
        host = (host or "").strip()
        if not host:
            continue
        if host.replace(".", "").isdigit():
            names.append(f"IP:{host}")
        else:
            names.append(f"DNS:{host}")
    if "DNS:localhost" not in names:
        names.append("DNS:localhost")
    if "IP:127.0.0.1" not in names:
        names.append("IP:127.0.0.1")
    cmd = [
        openssl,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-sha256",
        "-days",
        "825",
        "-nodes",
        "-keyout",
        str(key),
        "-out",
        str(cert),
        "-subj",
        "/CN=bill-rent-manager",
        "-addext",
        "subjectAltName=" + ",".join(names),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    try:
        key.chmod(0o600)
    except OSError:
        pass
    print(f"HTTPS certificate: {cert}", flush=True)
    return cert, key
