@echo off
REM ==========================================================================
REM  CIP one-click launcher (Windows). Double-click this file.
REM  Checks prerequisites, creates the database once, starts the API and the
REM  React app in their own windows, then opens http://localhost:5173
REM ==========================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set MISSING=

echo.
echo  Checking prerequisites...
python --version >nul 2>&1 || (echo   [X] Python 3.11+ not found.  Install: https://www.python.org/downloads/  ^(tick "Add python.exe to PATH"^) & set MISSING=1)
node --version   >nul 2>&1 || (echo   [X] Node.js 20+ not found.   Install: https://nodejs.org/  ^(LTS^) & set MISSING=1)

set "PSQL="
psql --version >nul 2>&1 && set "PSQL=psql"
if not defined PSQL for /d %%D in ("C:\Program Files\PostgreSQL\*") do if exist "%%D\bin\psql.exe" set "PSQL=%%D\bin\psql.exe"
if not defined PSQL (echo   [X] PostgreSQL 14+ not found. Install: https://www.postgresql.org/download/windows/ & set MISSING=1)

if defined MISSING (
  echo.
  echo  Install the missing tools above, close this window, then double-click run-all.bat again.
  pause
  exit /b 1
)
echo   [OK] Python, Node.js and PostgreSQL found.

if not exist ".db_ready" (
  echo.
  echo  Creating database cip_db ^(one time^). Enter the password of the "postgres" user
  echo  that you chose when installing PostgreSQL:
  "!PSQL!" -U postgres -h localhost -f setup-database.sql
  if errorlevel 1 (
    echo  [X] Database setup failed - see the message above.
    pause
    exit /b 1
  )
  echo ok> .db_ready
)

echo.
echo  Starting backend  ^(http://localhost:8000^) ...
start "CIP Backend - keep open" cmd /k call "%~dp0start-backend.bat"
echo  Starting frontend ^(http://localhost:5173^) ...
start "CIP Frontend - keep open" cmd /k call "%~dp0start-frontend.bat"

echo.
echo  First run installs packages - this can take 2-4 minutes.
echo  Waiting for the backend to answer...
:wait
timeout /t 5 /nobreak >nul
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing http://localhost:8000/api/health -TimeoutSec 3 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 goto wait

echo  Backend is up. Opening the app...
timeout /t 5 /nobreak >nul
start "" http://localhost:5173
echo  Done. Keep the two "CIP" windows open while you use the app.
pause
