#!/bin/sh
APP="$(CDPATH= cd "$(dirname "$0")" && pwd)"
cd "$APP" || exit 1
echo "Bill Rent Manager"
echo "This window does not pay a bill."
if ! command -v python3 >/dev/null 2>&1; then
  echo "Install Python 3.11 or 3.12, then run this again."
  echo "macOS: brew install python@3.12"
  echo "Ubuntu: sudo apt install python3 python3-venv python3-pip"
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv || exit 1
fi
.venv/bin/python -m pip install -r requirements.txt || exit 1
if ! command -v tesseract >/dev/null 2>&1; then
  echo "Tesseract was not found. Bill checks need it."
  echo "macOS: brew install tesseract"
  echo "Ubuntu/Debian: sudo apt install tesseract-ocr"
  echo "The app will still open. A bill check will say Tesseract is missing."
fi
echo "Leave this window open. Close it to stop Bill Rent Manager."
echo "On this computer: http://127.0.0.1:8765"
cd "$APP/.." || exit 1
exec "$APP/.venv/bin/python" -m ownerapp "$@"
