@echo off
rem For Windows Task Scheduler: no pause, and the exit code is passed on
rem (Last Run Result 0x0 = everything succeeded, 0x1 = something failed).
rem main.py writes the full log to logs\export_YYYYMMDD.log.
rem Errors from before that log exists (for example a broken .venv) go to logs\scheduler.log.
cd /d "%~dp0"
if not exist logs mkdir logs
if not exist .venv\Scripts\python.exe (
    echo %date% %time% .venv not found - run setup.bat first.>>logs\scheduler.log
    exit /b 1
)
.venv\Scripts\python main.py %* >nul 2>>logs\scheduler.log
exit /b %ERRORLEVEL%
