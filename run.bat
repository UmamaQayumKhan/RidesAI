@echo off
REM One-click start for Windows. Run setup steps from README the first time.
call .venv\Scripts\activate.bat
uvicorn app.main:app --reload --port 8000
