@echo off
cd /d "%~dp0"
set /a TRIES=0
:again
if exist "data\port.txt" goto found
set /a TRIES+=1
if %TRIES% GTR 40 exit /b 1
ping -n 2 127.0.0.1 >nul
goto again
:found
set /p PORT=<"data\port.txt"
start "" "http://127.0.0.1:%PORT%"
