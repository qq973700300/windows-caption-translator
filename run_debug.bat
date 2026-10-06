@echo off
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0realtime_subtitle\main.py"
echo.
echo [Exit code: %ERRORLEVEL%] see realtime_subtitle\subtitle.log
pause
