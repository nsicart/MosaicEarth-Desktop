[Setup]
AppId=MosaicEarth.Desktop.Local
AppName=MosaicEarth Desktop
AppVersion=0.1.2
DefaultDirName={localappdata}\Programs\MosaicEarth Desktop
DefaultGroupName=MosaicEarth Desktop
PrivilegesRequired=lowest
OutputDir=..\installers
OutputBaseFilename=MosaicEarth-Desktop-0.1.2-x64-Setup
LicenseFile=..\LICENSE
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=MosaicEarth Desktop

[Files]
Source: "..\src\*.py"; DestDir: "{app}\src"; Flags: ignoreversion
Source: "..\resources\*"; DestDir: "{app}\resources"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\build\windows-python\*"; DestDir: "{app}\python"; Flags: ignoreversion recursesubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_LICENSES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\build\SOURCE_REVISION"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\MosaicEarth Desktop"; Filename: "{app}\python\python.exe"; Parameters: """{app}\src\mosaicearth_desktop.py"""
