@echo off
cd /d "%~dp0"
echo Starting RoamDigi Proposal Sending Agent...
echo Open http://127.0.0.1:5000 in your browser.
echo (Close this window to stop the app.)
echo.
"%~dp0..\.venv\Scripts\python.exe" app.py
pause
