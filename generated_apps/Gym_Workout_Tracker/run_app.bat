@echo off
echo.
echo  Starting Gym_Workout_Tracker...
echo.
cd backend
pip install -r requirements.txt -q
echo Backend starting on port 8101...
start cmd /k "uvicorn main:app --port 8101"
timeout /t 3 /nobreak >nul
start http://127.0.0.1:8101
echo Done! App running at http://127.0.0.1:8101
