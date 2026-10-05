MSBTS 1.0 Alpha 26 - Windows Compact
=====================================

Ziel
----
Kompakte Windows-Ausgabe der aktuellen MSBTS-Badminton-Software inklusive
Purple-Design, Demo-Turnier und angepasster Präsentation.

Windows-EXE erstellen
---------------------
1. Diesen Ordner auf einen Windows-PC kopieren und entpacken.
2. Python 3 muss nur auf diesem Builder-PC installiert sein.
3. BUILD_MSBTS_WINDOWS_COMPACT.bat doppelklicken.
4. Warten, bis "FERTIG" erscheint.

Danach entstehen im selben Ordner:
- MSBTS_1.0_WINDOWS_COMPACT.zip
- MSBTS_1.0_WINDOWS_COMPACT_SETUP.exe (wenn IExpress verfügbar ist)

Die fertige MSBTS.exe benötigt beim Empfänger KEIN Python.

Kompakt/Sauber
--------------
- nur die für Windows benötigten Programmdateien
- keine Mac-/Linux-Builder
- keine __pycache__-Dateien
- keine alten Versionsarchive
- keine Turnier-/Benutzerdaten im Paket
- eine einzelne MSBTS.exe für die eigentliche Anwendung

Hinweis
-------
Windows Defender kann bei selbst erstellten, nicht digital signierten EXE-Dateien
eine SmartScreen-Warnung anzeigen. Das ist bei privaten PyInstaller-Builds möglich.
