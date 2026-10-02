@echo off
setlocal
cd /d "%~dp0"

python app.py
if errorlevel 1 (
    echo.
    echo Failed to start the server. Make sure Python is installed and run: pip install -r requirements.txt
    pause
)
