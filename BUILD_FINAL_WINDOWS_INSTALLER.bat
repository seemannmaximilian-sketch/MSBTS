@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title MSBTS 1.0 - Final Windows Installer Builder

echo =====================================================
echo  MSBTS 1.0 - Windows Installer erstellen
echo =====================================================
echo.
echo Dieser Builder ist nur fuer den Ersteller gedacht.
echo Der spaetere MSBTS-Nutzer braucht KEIN Python.
echo.

where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (
  where python >nul 2>nul
  if errorlevel 1 goto :nopython
  set "PY=python"
)

if exist ".buildvenv_win" rmdir /s /q ".buildvenv_win"
if exist "build_win" rmdir /s /q "build_win"
if exist "dist_win" rmdir /s /q "dist_win"
if exist "MSBTS_1.0_Setup.exe" del /q "MSBTS_1.0_Setup.exe"

echo [1/3] MSBTS.exe wird erstellt ...
%PY% -m venv .buildvenv_win
if errorlevel 1 goto :error
call .buildvenv_win\Scripts\activate.bat
python -m pip install --disable-pip-version-check -q --upgrade pip
python -m pip install --disable-pip-version-check -q -r requirements.txt "pyinstaller>=6.10,<7"
if errorlevel 1 goto :error
python -m PyInstaller launcher.py --name MSBTS --noconfirm --clean --windowed --onefile ^
  --paths src --distpath dist_win --workpath build_win\work --specpath build_win\spec ^
  --icon src\fts\resources\msbts_app_icon.ico ^
  --add-data "src\fts\resources;fts\resources" ^
  --add-data "src\fts\gui\assets;fts\gui\assets" ^
  --add-data "src\fts\gui\styles;fts\gui\styles" ^
  --hidden-import PySide6.QtPrintSupport --hidden-import PySide6.QtSvg --collect-all pypdf
if errorlevel 1 goto :error
if not exist "dist_win\MSBTS.exe" goto :error

echo [2/3] Installer-Compiler wird gesucht ...
set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC (
  where iscc.exe >nul 2>nul
  if not errorlevel 1 set "ISCC=iscc.exe"
)
if not defined ISCC goto :noinno

echo [3/3] MSBTS_1.0_Setup.exe wird erstellt ...
"%ISCC%" MSBTS_SETUP.iss
if errorlevel 1 goto :error
if not exist "MSBTS_1.0_Setup.exe" goto :error

echo.
echo =====================================================
echo  FERTIG!
echo =====================================================
echo Weitergeben musst du nur diese Datei:
echo.
echo   %CD%\MSBTS_1.0_Setup.exe
echo.
echo Der Empfaenger braucht KEIN Python.
echo.
pause
exit /b 0

:noinno
echo.
echo Inno Setup 6 ist noch nicht installiert.
echo Bitte einmal Inno Setup 6 installieren und diesen Builder danach erneut starten.
echo Die bereits gebaute dist_win\MSBTS.exe bleibt erhalten.
echo.
pause
exit /b 2

:nopython
echo.
echo Auf DIESEM Builder-PC fehlt Python 3.
echo Python wird nur zum Erstellen des Installers benoetigt.
echo Der spaetere Nutzer braucht kein Python.
echo.
pause
exit /b 1

:error
echo.
echo FEHLER beim Erstellen. Bitte den sichtbaren Fehlertext fotografieren.
echo.
pause
exit /b 1
