@echo off
echo.
echo  ========================
echo   IdeaForge — App Forge
echo  ========================
echo.
pip install -r requirements.txt -q
echo Starting IdeaForge server...
start http://127.0.0.1:8000
uvicorn main:app --reload --host 127.0.0.1 --port 8000
