@echo off
echo.
echo  Starting Habit_Tracker...
echo.
cd backend
pip install -r requirements.txt -q
echo Backend starting on port 8100...
start cmd /k "uvicorn main:app --port 8100"
timeout /t 3 /nobreak >nul
start http://127.0.0.1:8100
echo Done! App running at http://127.0.0.1:8100
