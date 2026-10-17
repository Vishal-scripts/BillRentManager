#!/usr/bin/env python3
"""Clean ZIP for the owner. No database, no .env, no real tenant list."""

from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
ZIP_NAME = "narela-rent-handover.zip"

INCLUDE_FILES = (
    "README.md",
    "docs/BILL_CHECK.md",
    "docs/TEST_GUIDE.md",
    "docs/OWNER_GUIDE.md",
    "users.env.example",
    "data/sample_tenants.csv",
    "ownerapp/requirements.txt",
    "ownerapp/.env.example",
)

SKIP_PARTS = {".env", ".venv", "__pycache__", "logs", "debug"}
SKIP_SUFFIX = {".pyc", ".db"}


def _allowed(path: Path) -> bool:
    if path.name == ".env" or path.suffix in SKIP_SUFFIX:
        return False
    return not any(part in SKIP_PARTS or part.endswith(".pyc") for part in path.parts)


def handover_paths(root: Path = ROOT) -> list[Path]:
    paths = []
    seen = set()

    def add(path: Path) -> None:
        if not path.is_file() or not _allowed(path):
            return
        key = path.resolve()
        if key in seen:
            return
        seen.add(key)
        paths.append(path)

    for rel in INCLUDE_FILES:
        add(root / rel)
    for folder in ("ownerapp", "billcheck"):
        base = root / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            add(path)
    return paths


def build_zip(dest: Path | None = None, root: Path = ROOT) -> Path:
    dest = dest or (DIST / ZIP_NAME)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in handover_paths(root):
            archive.write(path, Path("narela-rent") / path.relative_to(root))
    return dest


def main() -> None:
    import sys

    if "--portable" in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from build_windows_portable import main as build_portable

        build_portable()
        return
    print(f"Wrote {build_zip()}")


if __name__ == "__main__":
    main()
