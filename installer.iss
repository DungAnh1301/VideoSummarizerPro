#define MyAppName "Video Summarizer Pro"
#define MyAppVersion "1.1"

[Setup]
AppId={{8F3C2A91-7D14-4B6E-9E20-A1B2C3D4E5F6}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\VideoSummarizerPro
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=VideoSummarizerPro_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
AllowNoIcons=yes
UninstallDisplayName={#MyAppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Tạo shortcut trên Desktop"; GroupDescription: "Shortcut:"; Flags: checkedonce

[Files]
Source: "build\payload\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "broll_semantic.py"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autodesktop}\Video Summarizer Pro"; Filename: "{app}\Chay_App.bat"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{group}\Video Summarizer Pro"; Filename: "{app}\Chay_App.bat"; WorkingDir: "{app}"

[Run]
Filename: "{app}\setup.exe"; Parameters: "--no-pause"; StatusMsg: "Dang cai Python, thu vien, FFmpeg, CapCut TTS..."; Flags: waituntilterminated
Filename: "{app}\Chay_App.bat"; Description: "Mo app ngay"; Flags: nowait postinstall skipifsilent
