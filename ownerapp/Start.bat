@echo off
cd /d "%~dp0"
echo Bill Rent Manager
echo This window does not pay a bill.
echo.
where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
%PY% --version >nul 2>&1
if errorlevel 1 (
  echo Install Python 3.11 or 3.12 from https://www.python.org/downloads/windows/
  echo Tick Add python.exe to PATH, then double-click Start.bat again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv
  if errorlevel 1 (
    echo Could not create .venv
    pause
    exit /b 1
  )
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Could not install the libraries.
  pause
  exit /b 1
)
set "TESS="
where tesseract >nul 2>&1 && set "TESS=tesseract"
if not defined TESS if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" set "TESS=%ProgramFiles%\Tesseract-OCR\tesseract.exe"
if not defined TESS if exist "%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe" set "TESS=%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe"
if not defined TESS if exist "%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe" set "TESS=%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"
if not defined TESS (
  echo Tesseract was not found. Installing it now...
  winget install --id UB-Mannheim.TesseractOCR -e --accept-package-agreements --accept-source-agreements
  if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" set "TESS=%ProgramFiles%\Tesseract-OCR\tesseract.exe"
  if not defined TESS if exist "%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe" set "TESS=%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe"
  if not defined TESS if exist "%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe" set "TESS=%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"
)
if defined TESS (
  set "TESSERACT_CMD=%TESS%"
) else (
  echo Tesseract is still missing. The app will open and show a fix page.
  echo You can install it later with: winget install UB-Mannheim.TesseractOCR
  echo.
)
echo Leave this window open. Close it to stop Bill Rent Manager.
echo People who can sign in are listed in users.env next to the ownerapp folder.
echo Open that file in Notepad, add or delete a line, save, then restart.
echo On this PC: http://127.0.0.1:8765
cd /d "%~dp0.."
"%~dp0.venv\Scripts\python.exe" -m ownerapp %*
if errorlevel 1 pause
exit /b %ERRORLEVEL%
