#define MyAppName "STOCK by NOVARIX"
#define MyAppVersion "1.0.1"
#define MyAppPublisher "NOVARIX"
#define MyAppExeName "STOCK by NOVARIX.exe"

[Setup]
AppId={{A7F3E8D1-4B2A-4C6E-9F12-8A3B5C7D9E0F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\NOVARIX\STOCK
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
DisableProgramGroupPage=yes
OutputDir=D:\NOVARIX STOCK\dist-installer
OutputBaseFilename=STOCK by NOVARIX SETUP
SetupIconFile=D:\NOVARIX STOCK\assets\stock-icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName}
DisableDirPage=no
DirExistsWarning=no

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear un acceso directo en el &Escritorio"; GroupDescription: "Accesos directos:"
Name: "startmenuicon"; Description: "Crear acceso directo en el menú &Inicio"; GroupDescription: "Accesos directos:"

[Files]
Source: "D:\NOVARIX STOCK\dist\STOCK by NOVARIX\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Desinstalar {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir {#MyAppName}"; Flags: nowait postinstall skipifsilent
