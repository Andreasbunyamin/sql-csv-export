@echo off
rem Loads the Kaggle Walmart dataset into the PostgreSQL database set in .env.
rem   load_demo_data.bat                             looks in demo\data\ (Walmart.csv or the Kaggle zip)
rem   load_demo_data.bat "C:\Downloads\archive.zip"  or give the path of the file
cd /d "%~dp0"
.venv\Scripts\python demo\load_walmart.py %*
pause
