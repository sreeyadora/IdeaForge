@echo off
setlocal
echo.
echo  ========================================
echo   IdeaForge — Starting Fitness_Tracker
echo  ========================================
echo.

cd /d "%~dp0backend"

echo [1/3] Installing dependencies...
pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo ERROR: pip install failed. Check your Python/pip setup.
    pause
    exit /b 1
)
echo       Dependencies OK
echo.

echo [2/3] Starting backend on port 8101...
echo       URL: http://127.0.0.1:8101
echo       Press Ctrl+C to stop
echo.

echo [3/3] Opening browser...
timeout /t 2 /nobreak >nul
start http://127.0.0.1:8101

uvicorn main:app --host 127.0.0.1 --port 8101 --reload

echo.
echo  Server stopped.
pause
