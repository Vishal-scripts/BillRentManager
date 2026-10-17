#!/usr/bin/env python3
"""Build BillRentManager-windows-x64.zip (embeddable CPython, wheels, Tesseract).

No Playwright/Chromium: the bill check uses requests and Tesseract.
See docs/PORTABLE.md.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "build" / "cache"
STAGE = ROOT / "build" / "BillRentManager"
DIST = ROOT / "dist" / "BillRentManager-windows-x64.zip"
ARTIFACT = Path("/opt/cursor/artifacts/BillRentManager-windows-x64.zip")
SPLIT = Path("/opt/cursor/artifacts/split")

PYTHON_EMBED = "https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip"
TESSERACT_SETUP = (
    "https://github.com/UB-Mannheim/tesseract/releases/download/"
    "v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
)
# Used only if the installer did not contain eng.traineddata.
ENG_TRAINEDDATA = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/eng.traineddata"

REQUIREMENTS = [
    "fastapi==0.115.6",
    "uvicorn==0.34.0",
    "itsdangerous==2.2.0",
    "python-multipart==0.0.20",
    "openpyxl==3.1.5",
    "httpx==0.28.1",
    "requests==2.32.3",
    "pytesseract==0.3.13",
    "pillow==11.3.0",
    "pydantic==2.10.6",
]


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0:
        print(f"cached {dest.name}", flush=True)
        return
    run(["curl", "-fL", "--retry", "3", "-o", str(dest), url])


def to_crlf(src: Path, dest: Path) -> None:
    text = src.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
    dest.write_bytes(text.encode("utf-8"))


def copy_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        src,
        dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", ".env", "logs", "debug"),
    )


def download_wheels(folder: Path) -> None:
    if any(folder.glob("*.whl")):
        print(f"cached wheels in {folder}", flush=True)
        return
    folder.mkdir(parents=True, exist_ok=True)
    run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--dest",
            str(folder),
            "--only-binary=:all:",
            "--python-version",
            "312",
            "--platform",
            "win_amd64",
            "--implementation",
            "cp",
            "--abi",
            "cp312",
            *REQUIREMENTS,
        ]
    )


def wanted_wheel(name: str) -> bool:
    lower = name.lower()
    if lower.endswith(".whl") is False:
        return False
    if "macosx" in lower or "manylinux" in lower or "linux" in lower:
        return False
    if "win32" in lower and "win_amd64" not in lower:
        return False
    return True


def install_wheels(wheels: Path, site: Path) -> None:
    site.mkdir(parents=True, exist_ok=True)
    files = [path for path in wheels.glob("*.whl") if wanted_wheel(path.name)]
    if not files:
        raise SystemExit(f"No Windows wheels in {wheels}")
    for wheel in files:
        print(f"wheel {wheel.name}", flush=True)
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(site)


def extract_tesseract(installer: Path, dest: Path) -> None:
    if (dest / "tesseract.exe").is_file() and (dest / "tessdata" / "eng.traineddata").is_file():
        print("cached tesseract tree", flush=True)
        return
    raw = CACHE / "tesseract-extracted"
    if raw.exists():
        shutil.rmtree(raw)
    raw.mkdir(parents=True)
    run(["7z", "x", "-y", f"-o{raw}", str(installer)])
    exe = next(raw.rglob("tesseract.exe"), None)
    if exe is None:
        raise SystemExit("The Tesseract installer did not contain tesseract.exe")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    # Copy the directory that holds tesseract.exe (dlls sit beside it).
    for item in exe.parent.iterdir():
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
    trained = dest / "tessdata" / "eng.traineddata"
    if not trained.is_file():
        trained.parent.mkdir(parents=True, exist_ok=True)
        download(ENG_TRAINEDDATA, trained)


def pe_imports(path: Path) -> list[str]:
    objdump = shutil.which("x86_64-w64-mingw32-objdump")
    if not objdump:
        raise SystemExit("Install binutils-mingw-w64-x86-64 so Tesseract can be trimmed.")
    out = subprocess.check_output([objdump, "-p", str(path)], text=True, errors="replace")
    names = []
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("DLL Name:"):
            names.append(stripped.split(":", 1)[1].strip())
    return names


def prune_tesseract(dest: Path) -> None:
    """Keep tesseract.exe, the DLLs it loads, and eng.traineddata.

    The UB Mannheim installer also ships training programs, extra languages,
    and GUI libraries. Those are not used to read a captcha.
    """
    exe = dest / "tesseract.exe"
    if not exe.is_file():
        raise SystemExit(f"Missing {exe}")
    by_lower = {path.name.lower(): path for path in dest.glob("*") if path.is_file()}
    keep: set[Path] = set()
    stack = ["tesseract.exe"]
    seen: set[str] = set()
    while stack:
        name = stack.pop()
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        path = by_lower.get(key)
        if path is None:
            continue
        keep.add(path.resolve())
        try:
            deps = pe_imports(path)
        except subprocess.CalledProcessError:
            continue
        stack.extend(deps)
    trained = (dest / "tessdata" / "eng.traineddata").resolve()
    if not trained.is_file():
        raise SystemExit("eng.traineddata missing after Tesseract extract")
    keep.add(trained)
    removed = 0
    for path in list(dest.rglob("*")):
        if path.is_file() and path.resolve() not in keep:
            path.unlink()
            removed += 1
    for path in sorted((p for p in dest.rglob("*") if p.is_dir()), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass
    print(f"tesseract runtime files: {len(keep)} (removed {removed})", flush=True)


def compile_launcher(dest: Path) -> None:
    compiler = shutil.which("x86_64-w64-mingw32-gcc")
    if not compiler:
        raise SystemExit("Install gcc-mingw-w64-x86-64 so BillRentManager.exe can be built.")
    run(
        [
            compiler,
            "-O2",
            "-s",
            "-mwindows",
            "-o",
            str(dest),
            str(ROOT / "windows" / "BillRentManager.c"),
            "-lws2_32",
            "-lshell32",
        ]
    )


def assemble() -> Path:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir(parents=True)
    embed_zip = CACHE / "python-embed.zip"
    download(PYTHON_EMBED, embed_zip)
    python_dir = STAGE / "python"
    python_dir.mkdir()
    with zipfile.ZipFile(embed_zip) as archive:
        archive.extractall(python_dir)
    pth = next(python_dir.glob("python*._pth"))
    pth.write_text("python312.zip\n.\nLib\\site-packages\nimport site\n", encoding="ascii")
    wheels = CACHE / "wheels"
    download_wheels(wheels)
    site = python_dir / "Lib" / "site-packages"
    install_wheels(wheels, site)
    # ._pth turns on isolated mode, which ignores PYTHONPATH. This file is
    # read from site-packages and is relative to that folder, so the ZIP
    # can be unzipped anywhere.
    (site / "narela.pth").write_text(
        "..\\..\\..\\app\n..\\..\\..\\app\\billcheck\n",
        encoding="ascii",
    )

    installer = CACHE / "tesseract-setup.exe"
    download(TESSERACT_SETUP, installer)
    extract_tesseract(installer, STAGE / "tesseract")
    prune_tesseract(STAGE / "tesseract")

    copy_tree(ROOT / "ownerapp", STAGE / "app" / "ownerapp")
    copy_tree(ROOT / "billcheck", STAGE / "app" / "billcheck")
    sample = STAGE / "sample"
    sample.mkdir()
    shutil.copy2(ROOT / "data" / "sample_tenants.csv", sample / "sample_tenants.csv")
    (STAGE / "data").mkdir()
    (STAGE / "data" / "logs").mkdir()

    to_crlf(ROOT / "windows" / "Start NarelaRentApp.bat", STAGE / "Start BillRentManager.bat")
    for name in ("Open-Browser.bat", "Demo Mode.bat", "Allow-Phone-Access.bat", "README-FIRST.txt"):
        to_crlf(ROOT / "windows" / name, STAGE / name)
    shutil.copy2(ROOT / "users.env.example", STAGE / "users.env.example")
    compile_launcher(STAGE / "BillRentManager.exe")
    return STAGE


def zip_tree(folder: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix == ".pyc" or "__pycache__" in path.parts:
                continue
            archive.write(path, Path("BillRentManager") / path.relative_to(folder))
    mode = dest.stat().st_mode
    dest.chmod(mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)


def split_zip(src: Path, dest_dir: Path, part_size: int = 23 * 1024 * 1024) -> None:
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir(parents=True)
    data = src.read_bytes()
    names = []
    for index, start in enumerate(range(0, len(data), part_size)):
        name = f"{src.name}.part{index}"
        (dest_dir / name).write_bytes(data[start : start + part_size])
        names.append(name)
        print(f"Wrote {dest_dir / name} ({(dest_dir / name).stat().st_size} bytes)", flush=True)
    join = "copy /b " + "+".join(names) + " " + src.name + "\r\n"
    (dest_dir / "JOIN.bat").write_bytes(join.encode("ascii"))
    print(f"Wrote {dest_dir / 'JOIN.bat'}", flush=True)


def main() -> None:
    folder = assemble()
    zip_tree(folder, DIST)
    print(f"Wrote {DIST} ({DIST.stat().st_size} bytes)", flush=True)
    split_dir = ROOT / "dist" / "split"
    split_zip(DIST, split_dir)
    if ARTIFACT.parent.is_dir():
        # The artifact store accepts at most 100 MiB. copy2 also fails there (EINVAL).
        limit = 100 * 1024 * 1024
        if DIST.stat().st_size > limit:
            raise SystemExit(
                f"{DIST} is {DIST.stat().st_size} bytes, over the 100 MiB artifact limit"
            )
        with DIST.open("rb") as src, ARTIFACT.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        print(f"Wrote {ARTIFACT} ({ARTIFACT.stat().st_size} bytes)", flush=True)
        if SPLIT.exists():
            shutil.rmtree(SPLIT)
        SPLIT.mkdir(parents=True)
        for part in sorted(split_dir.iterdir()):
            target = SPLIT / part.name
            with part.open("rb") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
            print(f"Wrote {target} ({target.stat().st_size} bytes)", flush=True)


if __name__ == "__main__":
    main()
