# Portable Windows package

`scripts/build_windows_portable.py` builds one ZIP for Windows 10/11 64-bit. The owner unzips it and double-clicks `BillRentManager.exe`. They do not install Python or Tesseract, and they do not type a command.

The small source ZIP from `scripts/build_handover_zip.py` is unchanged. Pass `--portable` to that script to build this package instead:

```bash
python3 scripts/build_handover_zip.py --portable
```

Or:

```bash
python3 scripts/build_windows_portable.py
```

The file is written to `/opt/cursor/artifacts/BillRentManager-windows-x64.zip` when that folder exists, and also to `dist/BillRentManager-windows-x64.zip`. Parts of 23 MB or smaller are written to `/opt/cursor/artifacts/split/` with `JOIN.bat`.

## What the build downloads

Cached under `build/cache/` (not committed):

- The official Windows embeddable CPython 3.12 x64 zip from python.org
- win_amd64 / pure-Python wheels for the app’s libraries (`pip download`)
- The UB Mannheim Tesseract 64-bit installer, extracted with 7-Zip. The package keeps `tesseract.exe`, the DLLs it loads, and `tessdata\eng.traineddata`. Training programs and other languages are left out.

The script also compiles `windows/BillRentManager.c` with `x86_64-w64-mingw32-gcc` into `BillRentManager.exe`. Packages needed on the build machine: `p7zip-full` and `gcc-mingw-w64-x86-64`.

Playwright and Chromium are not included. The bill check uses `requests` and Tesseract, not a browser.

## Folder inside the ZIP

```text
BillRentManager\
  BillRentManager.exe
  users.env          created on first start
  Start BillRentManager.bat
  Demo Mode.bat
  Allow-Phone-Access.bat
  README-FIRST.txt
  app\            ownerapp and billcheck
  python\         embeddable CPython and installed wheels
  tesseract\      tesseract.exe and tessdata\eng.traineddata
  sample\         sample_tenants.csv
  data\           created at run time (SQLite, logs). Empty in the ZIP
```

`BillRentManager.exe` sets `NARELA_HOME` to its own folder, `TESSERACT_CMD` to `tesseract\tesseract.exe`, and `TESSDATA_PREFIX` to `tesseract\tessdata` (the folder that contains `eng.traineddata`). The embeddable Python ignores `PYTHONPATH`, so `python\Lib\site-packages\narela.pth` adds the `app` folder to the module path. The database is `data\owner.db` next to the EXE. Demo mode uses `data\owner-demo.db`. Usernames and PINs are in `users.env` in that same folder.

On every start the app checks that the libraries import, that Tesseract can read a test image, and that the data folder is writable. If port 8765 is busy it uses the next free port and prints it. If a check fails, the browser shows a fix page and a bill run does not start.

## First run

If `users.env` has no user, the browser shows a setup page: owner name, PIN, the email Tata Power’s form requires, and a 10-digit mobile for the e-receipt. The PIN is written as `admin=PIN` in `users.env`. `Allow-Phone-Access.bat` asks Windows for administrator once and adds a firewall rule for ports 8765 to 8784. The main program never asks for administrator.
