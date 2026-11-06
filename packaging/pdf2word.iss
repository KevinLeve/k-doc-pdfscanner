; Inno Setup script for K-Doc PDFScanner (free, open-source; AGPL-3.0 - see licenses\README.md
; and https://github.com/KevinLeve/k-doc-pdfscanner for full third-party license detail).
;
; Build the app first:
;   .venv\Scripts\pyinstaller --noconfirm --clean packaging\pdf2word.spec --distpath dist-final --workpath build-final
; Then compile this script (adjust the ISCC path if Inno Setup is installed elsewhere):
;   & "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" packaging\pdf2word.iss

#define MyAppName "K-Doc PDFScanner"
#define MyAppVersion "0.4.0"
#define MyAppPublisher "Kevin Bastin"
#define MyAppExeName "pdf2word.exe"
#define MyAppCliName "pdf2word-cli.exe"
#define SourceDir "..\dist-final\pdf2word"
#define MyAppIcon "assets\kdoc_pdfscanner.ico"

[Setup]
; New AppId for the K-Doc PDFScanner rebrand - deliberately NOT the old "PDF to Word" pilot's id,
; so this is treated as a fresh install (own folder, own registry entry) rather than an in-place
; upgrade that would otherwise keep reusing the old install's remembered directory name/location.
AppId={{63DBA305-55D6-4DD3-A291-9D41681980B9}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist-installer
OutputBaseFilename=KDocPDFScanner-Setup-{#MyAppVersion}
SetupIconFile={#MyAppIcon}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\licenses\INSTALLER-NOTICE.txt
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoProductName={#MyAppName}
VersionInfoDescription={#MyAppName} {#MyAppVersion} Setup
VersionInfoCopyright={#MyAppPublisher}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
