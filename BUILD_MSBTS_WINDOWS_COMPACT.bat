@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title MSBTS 1.0 - Windows Compact Builder

echo =====================================================
echo  MSBTS 1.0 Alpha 26 - Windows Compact
echo =====================================================
echo.

echo Dieses Skript erstellt eine kompakte Windows-EXE.
echo Turnier- und Benutzerdaten werden NICHT mit eingebaut.
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  where python >nul 2>nul
  if errorlevel 1 goto :nopython
  set "PY=python"
)

rem Alte Build-Reste entfernen
if exist ".buildvenv_win" rmdir /s /q ".buildvenv_win"
if exist "build_win" rmdir /s /q "build_win"
if exist "dist_win" rmdir /s /q "dist_win"
if exist "MSBTS_Windows_Compact" rmdir /s /q "MSBTS_Windows_Compact"

rem Sicherheitsregel: keine Turnierdaten in die Weitergabe aufnehmen
if exist "Aktuelles_Turnier" rmdir /s /q "Aktuelles_Turnier"
if exist "portable_data" rmdir /s /q "portable_data"
if exist "fts.db" del /q "fts.db"

echo [1/4] Build-Umgebung wird vorbereitet ...
%PY% -m venv .buildvenv_win
if errorlevel 1 goto :error
call .buildvenv_win\Scripts\activate.bat
python -m pip install --disable-pip-version-check -q --upgrade pip
if errorlevel 1 goto :error
python -m pip install --disable-pip-version-check -q -r requirements.txt "pyinstaller>=6.10,<7"
if errorlevel 1 goto :error

echo [2/4] MSBTS.exe wird gebaut ...
python -m PyInstaller launcher.py --name MSBTS --noconfirm --clean --windowed --onefile ^
  --paths src ^
  --distpath dist_win ^
  --workpath build_win\work ^
  --specpath build_win\spec ^
  --icon "%CD%\src\fts\resources\msbts_app_icon.ico" ^
  --add-data "%CD%\src\fts\resources;fts\resources" ^
  --add-data "%CD%\src\fts\gui\assets;fts\gui\assets" ^
  --add-data "%CD%\src\fts\gui\styles;fts\gui\styles" ^
  --hidden-import PySide6.QtPrintSupport ^
  --hidden-import PySide6.QtSvg ^
  --collect-all pypdf
if errorlevel 1 goto :error
if not exist "dist_win\MSBTS.exe" goto :error

mkdir "MSBTS_Windows_Compact"
copy /y "dist_win\MSBTS.exe" "MSBTS_Windows_Compact\MSBTS.exe" >nul
> "MSBTS_Windows_Compact\README.txt" echo MSBTS 1.0 Alpha 26 - Windows Compact
>> "MSBTS_Windows_Compact\README.txt" echo.
>> "MSBTS_Windows_Compact\README.txt" echo Start: MSBTS.exe doppelklicken.
>> "MSBTS_Windows_Compact\README.txt" echo Die Ausgabe enthaelt keine persoenlichen Turnierdaten.
>> "MSBTS_Windows_Compact\README.txt" echo Die Daten werden im Windows-Benutzerprofil gespeichert.

echo [3/4] Weitergabe-ZIP wird erstellt ...
set "ZIP=%CD%\MSBTS_1.0_WINDOWS_COMPACT.zip"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path '%CD%\MSBTS_Windows_Compact\*' -DestinationPath '%ZIP%' -Force"
if errorlevel 1 goto :error

echo [4/4] Optionaler Ein-Datei-Installer ...
where iexpress.exe >nul 2>nul
if errorlevel 1 goto :noiexpress

set "PKG=%TEMP%\MSBTS_COMPACT_%RANDOM%"
mkdir "%PKG%" >nul 2>nul
copy /y "MSBTS_Windows_Compact\MSBTS.exe" "%PKG%\MSBTS.exe" >nul

> "%PKG%\install.cmd" echo @echo off
>> "%PKG%\install.cmd" echo setlocal
>> "%PKG%\install.cmd" echo set "TARGET=%%LOCALAPPDATA%%\Programs\MSBTS"
>> "%PKG%\install.cmd" echo if not exist "%%TARGET%%" mkdir "%%TARGET%%"
>> "%PKG%\install.cmd" echo copy /Y "%%~dp0MSBTS.exe" "%%TARGET%%\MSBTS.exe" ^>nul
>> "%PKG%\install.cmd" echo powershell -NoProfile -ExecutionPolicy Bypass -Command "$w=New-Object -ComObject WScript.Shell; $d=$w.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\MSBTS.lnk'); $d.TargetPath=$env:LOCALAPPDATA+'\Programs\MSBTS\MSBTS.exe'; $d.WorkingDirectory=$env:LOCALAPPDATA+'\Programs\MSBTS'; $d.Save(); $m=$w.CreateShortcut($env:APPDATA+'\Microsoft\Windows\Start Menu\Programs\MSBTS.lnk'); $m.TargetPath=$env:LOCALAPPDATA+'\Programs\MSBTS\MSBTS.exe'; $m.WorkingDirectory=$env:LOCALAPPDATA+'\Programs\MSBTS'; $m.Save()"
>> "%PKG%\install.cmd" echo start "" "%%TARGET%%\MSBTS.exe"
>> "%PKG%\install.cmd" echo exit /b 0

set "SETUP=%CD%\MSBTS_1.0_WINDOWS_COMPACT_SETUP.exe"
set "SED=%TEMP%\msbts_compact_%RANDOM%.sed"
> "%SED%" echo [Version]
>> "%SED%" echo Class=IEXPRESS
>> "%SED%" echo SEDVersion=3
>> "%SED%" echo [Options]
>> "%SED%" echo PackagePurpose=InstallApp
>> "%SED%" echo ShowInstallProgramWindow=0
>> "%SED%" echo HideExtractAnimation=1
>> "%SED%" echo UseLongFileName=1
>> "%SED%" echo InsideCompressed=0
>> "%SED%" echo CAB_FixedSize=0
>> "%SED%" echo CAB_ResvCodeSigning=0
>> "%SED%" echo RebootMode=N
>> "%SED%" echo InstallPrompt=
>> "%SED%" echo DisplayLicense=
>> "%SED%" echo FinishMessage=MSBTS wurde installiert.
>> "%SED%" echo TargetName=%SETUP%
>> "%SED%" echo FriendlyName=MSBTS 1.0 Windows Compact
>> "%SED%" echo AppLaunched=install.cmd
>> "%SED%" echo PostInstallCmd=^<None^>
>> "%SED%" echo AdminQuietInstCmd=
>> "%SED%" echo UserQuietInstCmd=install.cmd
>> "%SED%" echo SourceFiles=SourceFiles
>> "%SED%" echo [SourceFiles]
>> "%SED%" echo SourceFiles0=%PKG%\
>> "%SED%" echo [SourceFiles0]
>> "%SED%" echo %%FILE0%%=
>> "%SED%" echo %%FILE1%%=
>> "%SED%" echo [Strings]
>> "%SED%" echo FILE0="MSBTS.exe"
>> "%SED%" echo FILE1="install.cmd"

iexpress.exe /N /Q "%SED%"
del /q "%SED%" >nul 2>nul
rmdir /s /q "%PKG%" >nul 2>nul

echo.
echo FERTIG:
echo   %ZIP%
if exist "%SETUP%" echo   %SETUP%
echo.
pause
exit /b 0

:noiexpress
echo IExpress ist nicht verfuegbar. Das ZIP ist trotzdem fertig:
echo   %ZIP%
echo.
pause
exit /b 0

:nopython
echo.
echo Python 3 wurde auf diesem Windows-PC nicht gefunden.
echo Nur zum EINMALIGEN Erstellen der EXE wird Python 3 benoetigt.
echo Die fertige MSBTS.exe braucht spaeter kein Python.
echo.
pause
exit /b 1

:error
echo.
echo FEHLER beim Erstellen der Windows-Version.
echo Bitte den Text in diesem Fenster fotografieren oder kopieren.
echo.
pause
exit /b 1
