@echo off
REM Double-click this to launch the Mixtape web app: frees port 5000, starts
REM the Flask server, and opens it in your browser. Close this window (or
REM press Ctrl+C) to stop the app.
cd /d "%~dp0"
python app.py
pause
