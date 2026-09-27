@echo off
cd /d "%~dp0"
rem   setup.bat           for PostgreSQL
rem   setup.bat vertica   also installs the Vertica driver
set "REQ=requirements.txt"
if /i "%~1"=="vertica" set "REQ=requirements-vertica.txt"
echo [1/3] Creating virtual environment .venv ...
python -m venv .venv || (echo Python not found. Install Python 3.9+ first. & pause & exit /b 1)
echo [2/3] Installing dependencies from %REQ% ...
.venv\Scripts\python -m pip install --upgrade pip >nul
.venv\Scripts\python -m pip install -r %REQ% || (pause & exit /b 1)
if not exist .env copy .env.example .env >nul
echo [3/3] Done. Check .env, then run test_connection.bat
pause
