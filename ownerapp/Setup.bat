@echo off
cd /d "%~dp0"
echo Bill Rent Manager setup. This does not pay a bill and does not send WhatsApp.
echo.
where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
%PY% --version >nul 2>&1
if errorlevel 1 (
  echo Install Python 3.11 or 3.12 from python.org and tick Add python.exe to PATH.
  goto fail
)
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv
  if errorlevel 1 goto fail
)
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
where tesseract >nul 2>&1
if %ERRORLEVEL%==0 (
  echo Tesseract is on PATH.
  set "TESS_LINE="
) else if exist "C:\Program Files\Tesseract-OCR\tesseract.exe" (
  echo Found Tesseract in Program Files.
  set "TESS_LINE=TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe"
) else (
  echo Tesseract was not found. Install it, then run Setup.bat again:
  echo   winget install UB-Mannheim.TesseractOCR
  set "TESS_LINE="
)
if exist ".env" (
  echo ownerapp\.env already exists. It was not overwritten.
  goto firewall
)
echo.
echo Choose a PIN of 4 to 8 digits. People on this Wi-Fi need it to open the app.
set /p OWNER_PIN=OWNER_PIN: 
echo Owner email for the Tata Power form. This is not a payment.
set /p RECEIPT_EMAIL=RECEIPT_EMAIL: 
echo Owner mobile, or leave blank
set /p RECEIPT_MOBILE=RECEIPT_MOBILE: 
(
  echo OWNER_PIN=%OWNER_PIN%
  echo PORT=8765
  echo RECEIPT_EMAIL=%RECEIPT_EMAIL%
  echo RECEIPT_MOBILE=%RECEIPT_MOBILE%
  echo REQUEST_DELAY_SECONDS=3
  echo NDPL_CAPTCHA_TRIES=6
) > ".env"
if defined TESS_LINE echo %TESS_LINE%>> ".env"
echo Wrote ownerapp\.env
:firewall
echo.
set /p FIRE=Allow port 8765 through Windows Firewall so a phone on this Wi-Fi can connect? Y/N: 
if /I "%FIRE%"=="Y" netsh advfirewall firewall add rule name="Narela rent 8765" dir=in action=allow protocol=TCP localport=8765
echo.
set /p TASK=Start the app every time you log in to Windows? Y/N: 
if /I "%TASK%"=="Y" call "%~dp0Install-Login-Task.bat"
echo.
echo Next: double-click Start-App.bat
echo Practice, with no Tata Power connection:
echo   .venv\Scripts\python.exe -m ownerapp --demo
goto end
:fail
echo Setup did not finish.
:end
pause
