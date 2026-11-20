@echo off
REM Restart the CIP portable app: stops ONLY the python.exe from this project's runtime folder,
REM waits for the old launcher to stop PostgreSQL, then starts run-portable.bat again.
cd /d "%~dp0"
title CIP restart
echo Stopping the running CIP app (this project's runtime only)...
powershell -NoProfile -Command "$rt = (Resolve-Path '%~dp0runtime').Path; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.ExecutablePath -like ($rt + '\*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
timeout /t 10 /nobreak >nul
start "CIP - Clinical Intake Platform (keep this window open)" cmd /c "%~dp0run-portable.bat"
exit
