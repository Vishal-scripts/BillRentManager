@echo off
cd /d "%~dp0"
set "TESSERACT_CMD=%~dp0tesseract\tesseract.exe"
set "TESSDATA_PREFIX=%~dp0tesseract\tessdata\"
set "PYTHONPATH=%~dp0app"
set "NARELA_HOME=%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"
echo Bill Rent Manager
echo This window does not pay a bill. Leave it open.
powershell -NoProfile -Command "try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',8765); $c.Close(); exit 0 } catch { exit 1 }"
if not errorlevel 1 (
  start "" "http://127.0.0.1:8765"
  exit /b 0
)
if exist "%~dp0data\port.txt" del "%~dp0data\port.txt"
start "" "%~dp0Open-Browser.bat"
"%~dp0python\python.exe" -m ownerapp
if errorlevel 1 pause
