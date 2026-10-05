@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
if not exist ".runvenv" %PY% -m venv .runvenv
call .runvenv\Scripts\activate.bat
python -m pip install --disable-pip-version-check -q -r requirements.txt
set "PYTHONPATH=%CD%\src"
python launcher.py
