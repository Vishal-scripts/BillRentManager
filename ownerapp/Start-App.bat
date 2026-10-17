@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run Setup.bat first.
  pause
  exit /b 1
)
if not exist ".env" (
  echo Missing ownerapp\.env. Run Setup.bat first.
  pause
  exit /b 1
)
echo Leave this window open. Close it to stop Bill Rent Manager.
cd /d "%~dp0.."
"%~dp0.venv\Scripts\python.exe" -m ownerapp %*
if errorlevel 1 pause
exit /b %ERRORLEVEL%
