@echo off
cd /d "%~dp0"
set "TASK=NarelaRentApp"
schtasks /Create /TN "%TASK%" /SC ONLOGON /RL LIMITED /F /TR "cmd.exe /c cd /d \"%~dp0\" && Start-App.bat"
if errorlevel 1 (
  echo Could not create the login task. Double-click Start-App.bat yourself.
  pause
  exit /b 1
)
echo Login task %TASK% will start the app when you sign in to Windows.
pause
