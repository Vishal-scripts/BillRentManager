"""Startup checks. A failed check stops bill runs before any CA is opened."""

from __future__ import annotations

from pathlib import Path


def probe_tesseract(cmd: str | None = None) -> dict:
    """Run Tesseract on a tiny image. A missing program is a fix, not a per-CA error."""
    from PIL import Image, ImageDraw

    import pytesseract

    chosen = cmd
    if chosen is None:
        from billcheck.captcha import configure_tesseract

        chosen = configure_tesseract() or ""
    if not chosen or not Path(chosen).is_file() and not _on_path(chosen):
        return {
            "ok": False,
            "detail": "Tesseract was not found.",
            "fix": "Install Tesseract, then start the app again. The portable folder already includes it. From a source folder, Start.bat installs it with winget.",
        }
    pytesseract.pytesseract.tesseract_cmd = chosen
    image = Image.new("L", (160, 48), 255)
    draw = ImageDraw.Draw(image)
    draw.text((16, 10), "8", fill=0)
    try:
        pytesseract.image_to_string(image, config="--psm 10 -c tessedit_char_whitelist=0123456789")
    except Exception as exc:
        return {
            "ok": False,
            "detail": str(exc).splitlines()[0][:240],
            "fix": "Tesseract did not run. Use the portable folder, or reinstall Tesseract and start the app again.",
        }
    return {"ok": True, "detail": f"Tesseract ran ({chosen}).", "fix": ""}


def _on_path(cmd: str) -> bool:
    from shutil import which

    return bool(cmd and which(cmd))


def _import_check(module: str, label: str) -> dict:
    try:
        __import__(module)
    except Exception as exc:
        return {
            "name": label,
            "ok": False,
            "detail": str(exc).splitlines()[0][:240],
            "fix": "Use the portable Bill Rent Manager folder, which includes this library.",
        }
    return {"name": label, "ok": True, "detail": "Installed.", "fix": ""}


def database_writable(db_path: Path) -> dict:
    folder = Path(db_path).parent
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write-test"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
    except OSError as exc:
        return {
            "name": "Database",
            "ok": False,
            "detail": str(exc),
            "fix": "The data folder is not writable. Unzip Bill Rent Manager to a folder you can write, such as Documents.",
        }
    return {"name": "Database", "ok": True, "detail": "The data folder is writable.", "fix": ""}


def run_checks(db_path: Path) -> dict:
    checks = [
        _import_check("PIL", "Pillow"),
        _import_check("pytesseract", "pytesseract"),
        _import_check("openpyxl", "openpyxl"),
        _import_check("requests", "requests"),
    ]
    tess = probe_tesseract()
    checks.append({"name": "Tesseract", **tess})
    checks.append(database_writable(db_path))
    failed = [item for item in checks if not item["ok"]]
    if failed:
        headline = failed[0]["fix"] or failed[0]["detail"]
    else:
        headline = "Ready."
    return {"ok": not failed, "headline": headline, "checks": checks}
