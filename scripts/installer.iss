; Inno Setup script for LoL Pacemaker (1-click per-user Windows installer)
; Built in GitHub Actions on windows-latest via:
;   iscc /DMyAppVersion=0.2.0 scripts/installer.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.2.0"
#endif

[Setup]
AppId={{B4F7A61C-7C4E-4B19-9D2F-5A6C8B3E91A2}
AppName=LoL Pacemaker
AppVersion={#MyAppVersion}
AppPublisher=LoL Pacemaker
AppPublisherURL=https://wpalswpa.github.io/lol-pacemaker/
AppSupportURL=https://github.com/wpalswpa/lol-pacemaker/issues
DefaultDirName={localappdata}\Programs\LoLPacemaker
DefaultGroupName=LoL Pacemaker
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=LoLPacemaker-Setup
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "portuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "autostart"; Description: "Start automatically with Windows (auto-popup when LoL match starts)"; GroupDescription: "Automation:"

[Files]
Source: "..\dist\LoLPacemaker.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\LoL Pacemaker"; Filename: "{app}\LoLPacemaker.exe"
Name: "{autodesktop}\LoL Pacemaker"; Filename: "{app}\LoLPacemaker.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "LoLPacemaker"; ValueData: """{app}\LoLPacemaker.exe"" --background"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\LoLPacemaker.exe"; Parameters: "--background"; Description: "{cm:LaunchProgram,LoL Pacemaker}"; Flags: nowait postinstall skipifsilent
