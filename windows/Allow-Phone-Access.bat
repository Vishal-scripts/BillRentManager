@echo off
cd /d "%~dp0"
net session >nul 2>&1
if errorlevel 1 (
  echo Windows will ask for permission. Press Yes.
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
netsh advfirewall firewall delete rule name="Bill Rent Manager" >nul 2>&1
netsh advfirewall firewall add rule name="Bill Rent Manager" dir=in action=allow protocol=TCP localport=8765-8784
echo.
echo Phone access is allowed on ports 8765 to 8784.
echo On the phone, open the address shown on the Home page.
echo This window is only the permission. Double-click BillRentManager.exe to run the app.
echo.
pause
