#define MyAppName "MSBTS"
#define MyAppVersion "1.0"
#define MyAppPublisher "M. Seemann"
#define MyAppExeName "MSBTS.exe"

[Setup]
AppId={{B05C80E6-8E12-4B90-A3F2-4A0DA4211B26}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\MSBTS
DefaultGroupName=MSBTS
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename=MSBTS_1.0_Setup
SetupIconFile=src\fts\resources\msbts_app_icon.ico
UninstallDisplayIcon={app}\MSBTS.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes

[Files]
Source: "dist_win\MSBTS.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\MSBTS"; Filename: "{app}\MSBTS.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\MSBTS"; Filename: "{app}\MSBTS.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Desktop-Verknuepfung erstellen"; GroupDescription: "Zusaetzliche Verknuepfungen:"; Flags: checkedonce

[Run]
Filename: "{app}\MSBTS.exe"; Description: "MSBTS jetzt starten"; Flags: nowait postinstall skipifsilent
