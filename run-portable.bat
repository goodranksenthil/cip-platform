@echo off
REM ==========================================================================
REM  CIP portable launcher - NO installs, NO admin rights needed.
REM  Uses the bundled runtime (Python 3.12 + PostgreSQL 16 + all packages).
REM  Double-click this file. Keep the window open while you use the app.
REM ==========================================================================
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"
set "RT=%ROOT%runtime"
set "PGDATA=%ROOT%pgdata"
set "PGPORT=5433"

title CIP - Clinical Intake Platform (keep this window open)

if not exist "%RT%\python.exe" (
  echo [1/4] Unpacking portable runtime - one time, about 1-2 minutes...
  if not exist "%RT%" mkdir "%RT%"
  tar -xf "%ROOT%cip-runtime-win64.zip" -C "%RT%"
  if errorlevel 1 ( echo [X] Could not unpack cip-runtime-win64.zip & pause & exit /b 1 )
) else (
  echo [1/4] Portable runtime found.
)

set "PATH=%RT%;%RT%\Library\bin;%RT%\Scripts;%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\WindowsPowerShell\v1.0"
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONNOUSERSITE=1"

if not exist "%PGDATA%\PG_VERSION" (
  echo [2/4] Creating local PostgreSQL data folder - one time...
  "%RT%\Library\bin\initdb.exe" -D "%PGDATA%" -U postgres --auth=trust --encoding=UTF8 --no-locale >"%ROOT%initdb.log" 2>&1
  if errorlevel 1 ( echo [X] initdb failed - see initdb.log & type "%ROOT%initdb.log" & pause & exit /b 1 )
) else (
  echo [2/4] PostgreSQL data folder found.
)

echo [3/4] Starting PostgreSQL on port %PGPORT%...
"%RT%\Library\bin\pg_ctl.exe" -D "%PGDATA%" -o "-p %PGPORT% -h 127.0.0.1" -l "%ROOT%postgres.log" -w status >nul 2>&1
if errorlevel 1 (
  "%RT%\Library\bin\pg_ctl.exe" -D "%PGDATA%" -o "-p %PGPORT% -h 127.0.0.1" -l "%ROOT%postgres.log" -w start >nul
  if errorlevel 1 ( echo [X] PostgreSQL did not start - see postgres.log & type "%ROOT%postgres.log" & pause & exit /b 1 )
)
"%RT%\Library\bin\psql.exe" -h 127.0.0.1 -p %PGPORT% -U postgres -q -c "CREATE USER cip WITH PASSWORD 'cip_password' SUPERUSER;" >nul 2>&1
"%RT%\Library\bin\psql.exe" -h 127.0.0.1 -p %PGPORT% -U postgres -q -c "CREATE DATABASE cip_db OWNER cip;" >nul 2>&1

set "CIP_DATABASE_URL=postgresql+psycopg://cip:cip_password@127.0.0.1:%PGPORT%/cip_db"
set "CIP_BLOB_ROOT=%ROOT%backend\storage\blob"
set "CIP_FIELD_ENCRYPTION_KEY=local-dev-only-rotate-me-0123456789"

echo [4/4] Starting the application at http://127.0.0.1:8000 ...
echo        (first start loads 40 demo cases - give it ~20 seconds)
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep 20; Start-Process 'http://127.0.0.1:8000'"
cd /d "%ROOT%backend"
"%RT%\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo Stopping PostgreSQL...
"%RT%\Library\bin\pg_ctl.exe" -D "%PGDATA%" -w stop >nul 2>&1
pause
