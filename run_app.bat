@echo off
rem Запуск интерфейса из окружения проекта (.venv), а если его нет - обычным python.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m streamlit run src\intro_paths\app.py
) else (
  python -m streamlit run src\intro_paths\app.py
)
