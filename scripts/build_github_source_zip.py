#!/usr/bin/env python3
"""Build BillRentManager-github-source.zip for a private GitHub repo.

Source only. No git history, virtualenvs, databases, logs, real tenant
lists, users.env, or the bundled Python and Tesseract binaries.
"""

from __future__ import annotations

import os
import stat
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "BillRentManager-github-source.zip"
ARTIFACT = Path("/opt/cursor/artifacts/BillRentManager-github-source.zip")
PREFIX = "BillRentManager"
LIMIT = 20 * 1024 * 1024

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "build",
    "dist",
    "logs",
    "debug",
    "node_modules",
}
SKIP_NAMES = {".env", "users.env", "tenants.csv"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".zip", ".exe"}


def included(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    if any(part in SKIP_DIRS for part in rel.parts):
        return False
    if path.name in SKIP_NAMES:
        return False
    if path.suffix.lower() in SKIP_SUFFIXES:
        return False
    if path.name.endswith(".jsonl"):
        return False
    return True


def source_files(root: Path = ROOT) -> list[Path]:
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if included(path):
                files.append(path)
    return files


def build_zip(dest: Path | None = None, root: Path = ROOT) -> Path:
    dest = dest or DIST
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in source_files(root):
            archive.write(path, Path(PREFIX) / path.relative_to(root))
    if dest.stat().st_size > LIMIT:
        raise SystemExit(f"{dest} is {dest.stat().st_size} bytes, over the 20 MB limit")
    return dest


def main() -> None:
    dest = build_zip()
    mode = dest.stat().st_mode
    dest.chmod(mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    print(f"Wrote {dest} ({dest.stat().st_size} bytes)")
    if ARTIFACT.parent.is_dir():
        with dest.open("rb") as src, ARTIFACT.open("wb") as out:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        print(f"Wrote {ARTIFACT} ({ARTIFACT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
