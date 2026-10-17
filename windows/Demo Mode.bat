@echo off
cd /d "%~dp0"
set "TESSERACT_CMD=%~dp0tesseract\tesseract.exe"
set "TESSDATA_PREFIX=%~dp0tesseract\tessdata\"
set "PYTHONPATH=%~dp0app"
set "NARELA_HOME=%~dp0"
set "PYTHONDONTWRITEBYTECODE=1"
powershell -NoProfile -Command "try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',8765); $c.Close(); exit 0 } catch { exit 1 }"
if not errorlevel 1 (
  echo The app is already open. Close its black window, then run Demo Mode again. Practice data uses the same window.
  pause
  exit /b 1
)
start "" cmd /c "ping -n 3 127.0.0.1 >nul & start http://127.0.0.1:8765"
"%~dp0python\python.exe" -m ownerapp --demo
if errorlevel 1 pause
