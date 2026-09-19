; Ekrano rašiklis — per-user installer (Lithuanian + English wizard).
; The app UI stays Lithuanian. This script only translates Setup screens.

#define AppName "Ekrano rašiklis"
#define AppNameAscii "EkranoRasiklis"
#define AppVersion "1.0.3"
#define AppPublisher "Ekrano rašiklis"
#define AppExeName "EkranoRasiklis.exe"

[Setup]
AppId={{8F3C2A91-6B47-4E1D-9C08-7A1B5D2E4F60}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\{#AppNameAscii}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename={#AppNameAscii}-Setup
SetupIconFile=..\ekrano_rasiklis.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=yes
LanguageDetectionMethod=uilanguage
MinVersion=10.0
CloseApplications=no
RestartApplications=no
UsePreviousTasks=yes
DisableWelcomePage=no
AllowNoIcons=no

[Languages]
Name: "lithuanian"; MessagesFile: "languages\Lithuanian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
lithuanian.WelcomeLabel2=Diegimo programa įdiegs „[name/ver]“ Jūsų kompiuteryje.
english.WelcomeLabel2=This will install [name/ver] on your computer.

[CustomMessages]
lithuanian.StartupTask=Paleisti prisijungus prie Windows
english.StartupTask=Run when Windows starts

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startup"; Description: "{cm:StartupTask}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\ekrano_rasiklis.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\ekrano_rasiklis.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\ekrano_rasiklis.ico"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\ekrano_rasiklis.ico"; Tasks: startup

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
