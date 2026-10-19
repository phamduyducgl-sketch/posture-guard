; Bộ cài PostureGuard cho Windows (Inno Setup 6). GitHub Actions chạy:
;   iscc /DAppVersion=1.0.0 packaging\installer.iss
; Cài vào thư mục của người dùng, không cần quyền quản trị.
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6E1B7C52-4F3A-4C8B-9E0D-2A5F7B3C1D84}
AppName=PostureGuard
AppVersion={#AppVersion}
AppPublisher=PostureGuard
DefaultDirName={localappdata}\Programs\PostureGuard
DefaultGroupName=PostureGuard
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=PostureGuard-Setup-Windows
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\PostureGuard.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force

[Tasks]
Name: "desktopicon"; Description: "Tạo biểu tượng trên màn hình Desktop"
Name: "startup"; Description: "Tự chạy PostureGuard khi mở máy (khuyên dùng)"

[Files]
Source: "..\dist\PostureGuard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PostureGuard"; Filename: "{app}\PostureGuard.exe"
Name: "{group}\Gỡ cài đặt PostureGuard"; Filename: "{uninstallexe}"
Name: "{autodesktop}\PostureGuard"; Filename: "{app}\PostureGuard.exe"; Tasks: desktopicon

[Registry]
; cùng mục mà menu "Tự chạy khi mở máy" trong app bật/tắt
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "PostureGuard"; ValueData: """{app}\PostureGuard.exe"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\PostureGuard.exe"; Description: "Mở PostureGuard ngay"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM PostureGuard.exe /F"; Flags: runhidden; RunOnceId: "KillApp"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
