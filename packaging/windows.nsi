Unicode true
; Release operators can supply an executable signing wrapper. It receives one
; file path, must sign and verify it, and return nonzero on any failure.
; Sign the embedded uninstaller before compression, then the outer installer.
!ifdef ME_SIGNING_COMMAND
  !uninstfinalize '"${ME_SIGNING_COMMAND}" "%1"' = 0
  !finalize '"${ME_SIGNING_COMMAND}" "%1"' = 0
!endif
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
!include "nsDialogs.nsh"
!include "WinVer.nsh"

Name "MosaicEarth Desktop"
OutFile "../installers/MosaicEarth-Desktop-0.4.5-x64-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\MosaicEarth Desktop"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetDateSave off
!define MUI_ICON "../resources/mosaicearth.ico"
!define MUI_UNICON "../resources/mosaicearth.ico"
VIProductVersion "0.4.5.0"
VIAddVersionKey "ProductName" "MosaicEarth Desktop"
VIAddVersionKey "FileDescription" "MosaicEarth Desktop Setup"
VIAddVersionKey "FileVersion" "0.4.5"
VIAddVersionKey "LegalCopyright" "MosaicEarth contributors"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\python\pythonw.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS '$\"$INSTDIR\windows-launch.pyw$\"'
!define MUI_FINISHPAGE_RUN_TEXT "Open MosaicEarth Desktop and set up local engines"
!define MUI_WELCOMEPAGE_TEXT "This wizard installs MosaicEarth Desktop for your Windows account.$\r$\n$\r$\nThe official installer requires the MosaicEarth account that purchased Desktop or has an active Studio plan. Two computers can be activated. Local processing uses Docker Desktop with Linux containers; the application guides you through engine setup.$\r$\n$\r$\nThe AGPL source edition can be built and used without an account."

Var AccountDialog

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "../LICENSE"
Page custom AccountPage AccountLeave
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE PrepareUpdate
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "MosaicEarth Desktop requires Windows x64."
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP "MosaicEarth Desktop requires Windows 10 or newer with Docker Desktop and WSL 2."
    Abort
  ${EndIf}
  IfSilent 0 +3
    MessageBox MB_ICONSTOP "Use the graphical wizard and activate your account on first launch." /SD IDOK
    Abort
  InitPluginsDir
  SetOutPath "$PLUGINSDIR\python"
  File /r "../build/windows-python/*"
  SetOutPath "$PLUGINSDIR\src"
  File "../src/mosaicearth_update.py"
FunctionEnd

Function PrepareUpdate
  nsExec::ExecToStack '"$PLUGINSDIR\python\python.exe" "$PLUGINSDIR\src\mosaicearth_update.py" "$INSTDIR"'
  Pop $0
  Pop $1
  ${If} $0 != "0"
    MessageBox MB_ICONEXCLAMATION "$1"
    Abort
  ${EndIf}
FunctionEnd

Function AccountPage
  !insertmacro MUI_HEADER_TEXT "Connect your account in the browser" "Google, GitHub, Microsoft or your MosaicEarth email."
  nsDialogs::Create 1018
  Pop $AccountDialog
  ${If} $AccountDialog == error
    Abort
  ${EndIf}
  ${NSD_CreateLabel} 0 0 100% 46u "After installation, MosaicEarth opens account activation in your browser. Use the account with your Desktop purchase or Studio plan. You can also use your MosaicEarth email and password."
  Pop $0
  ${NSD_CreateLabel} 0 58u 100% 46u "The local workspace and processing engines remain locked until this computer is activated. An existing valid activation is preserved when updating."
  Pop $0
  ${NSD_CreateLabel} 0 116u 100% 36u "Cloud synchronization is optional and has its own visible account connection. The source edition works locally without an account."
  Pop $0
  nsDialogs::Show
FunctionEnd

Function AccountLeave
  ; Activation occurs in the accessible first-run browser flow. Both the host
  ; and full local runtime verify the signed device grant before permitting use.
FunctionEnd

Section "MosaicEarth Desktop"
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  File "../LICENSE"
  File "../NOTICE"
  File "../build/NSIS-COPYRIGHT"
  File "../THIRD_PARTY_LICENSES.md"
  File "../INSTALL_WINDOWS.md"
  File "../README.md"
  File "../GPU.md"
  File "../INSTALL_LINUX.md"
  File "../BUILD.md"
  File "../CONNECTORS.md"
  File "../SIGNING.md"
  File "../RELEASE_NOTES.md"
  File "../MAP_SERVICES.md"
  File "../build/SOURCE_REVISION"
  File "windows-launch.pyw"
  SetOutPath "$INSTDIR\src"
  File "../src/*.py"
  SetOutPath "$INSTDIR\resources"
  File /r "../resources/*"
  FileOpen $0 "$INSTDIR\resources\edition-commercial" w
  FileWrite $0 "Official account-associated installer"
  FileClose $0
  SetOutPath "$INSTDIR\python"
  File /r "../build/windows-python/*"
  SetOutPath "$INSTDIR"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\MosaicEarth Desktop"
  CreateShortCut "$SMPROGRAMS\MosaicEarth Desktop\MosaicEarth Desktop.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\windows-launch.pyw"' "$INSTDIR\resources\mosaicearth.ico"
  CreateShortCut "$DESKTOP\MosaicEarth Desktop.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\windows-launch.pyw"' "$INSTDIR\resources\mosaicearth.ico"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MosaicEarthDesktop" "DisplayName" "MosaicEarth Desktop"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MosaicEarthDesktop" "DisplayIcon" "$INSTDIR\resources\mosaicearth.ico"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MosaicEarthDesktop" "DisplayVersion" "0.4.5"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MosaicEarthDesktop" "UninstallString" '"$INSTDIR\Uninstall.exe"'
SectionEnd

Function un.onInit
  ; A loaded pythonw runtime prevents Windows from removing its DLLs. Check and
  ; close only this installation's idle launcher, using the same ownership and
  ; active-transfer checks as an upgrade. Project data and Docker jobs remain.
  IfFileExists "$INSTDIR\python\python.exe" 0 uninstallCheckDone
  IfFileExists "$INSTDIR\src\mosaicearth_update.py" 0 uninstallCheckDone
  nsExec::ExecToStack '"$INSTDIR\python\python.exe" "$INSTDIR\src\mosaicearth_update.py" "$INSTDIR"'
  Pop $0
  Pop $1
  ${If} $0 != "0"
    MessageBox MB_ICONEXCLAMATION "$1"
    Abort
  ${EndIf}
  uninstallCheckDone:
FunctionEnd

Section "Uninstall"
  SetShellVarContext current
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MosaicEarthDesktop"
  Delete "$SMPROGRAMS\MosaicEarth Desktop\MosaicEarth Desktop.lnk"
  Delete "$DESKTOP\MosaicEarth Desktop.lnk"
  RMDir "$SMPROGRAMS\MosaicEarth Desktop"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\NOTICE"
  Delete "$INSTDIR\NSIS-COPYRIGHT"
  Delete "$INSTDIR\THIRD_PARTY_LICENSES.md"
  Delete "$INSTDIR\INSTALL_WINDOWS.md"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\GPU.md"
  Delete "$INSTDIR\INSTALL_LINUX.md"
  Delete "$INSTDIR\BUILD.md"
  Delete "$INSTDIR\CONNECTORS.md"
  Delete "$INSTDIR\SIGNING.md"
  Delete "$INSTDIR\RELEASE_NOTES.md"
  Delete "$INSTDIR\SOURCE_REVISION"
  Delete "$INSTDIR\windows-launch.pyw"
  RMDir /r "$INSTDIR\src"
  RMDir /r "$INSTDIR\resources"
  RMDir /r "$INSTDIR\python"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd
