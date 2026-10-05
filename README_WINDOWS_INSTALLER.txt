MSBTS 1.0 - Easy Windows Installer Builder
===========================================

Ziel: Eine einzige Datei MSBTS_1.0_Setup.exe fuer normale Windows-Nutzer.
Der Endnutzer benoetigt weder Python noch technische Kenntnisse.

ERSTELLER-PC (einmalig):
1. Python 3 installieren.
2. Inno Setup 6 installieren.
3. BUILD_FINAL_WINDOWS_INSTALLER.bat doppelklicken.
4. Nach erfolgreichem Build nur MSBTS_1.0_Setup.exe weitergeben.

ENDNUTZER:
Nur MSBTS_1.0_Setup.exe doppelklicken und dem Assistenten folgen.

Der Installer installiert pro Benutzer nach %%LOCALAPPDATA%%\Programs\MSBTS,
erstellt Startmenue/optional Desktop-Verknuepfung und bietet eine saubere Deinstallation.
