Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
!include "nsDialogs.nsh"
!include "WinVer.nsh"

Name "MosaicEarth Desktop"
OutFile "../installers/MosaicEarth-Desktop-0.2.0-x64-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\MosaicEarth Desktop"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetDateSave off
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\python\pythonw.exe"
!define MUI_FINISHPAGE_RUN_PARAMETERS '$\"$INSTDIR\src\mosaicearth_desktop.py$\"'
!define MUI_FINISHPAGE_RUN_TEXT "Open MosaicEarth Desktop and set up local engines"
!define MUI_WELCOMEPAGE_TEXT "This wizard installs MosaicEarth Desktop for your Windows account.$\r$\n$\r$\nThe official installer requires the MosaicEarth account that purchased Desktop or has an active Studio plan. Two computers can be activated. Local processing uses Docker Desktop with Linux containers; the application guides you through engine setup.$\r$\n$\r$\nThe AGPL source edition can be built and used without an account."

Var AccountDialog
Var EmailField
Var PasswordField
Var EmailValue
Var PasswordValue
Var CredentialsFile
Var ActivationResult
Var ActivationOutput
Var Activated

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "../LICENSE"
Page custom AccountPage AccountLeave
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
    MessageBox MB_ICONSTOP "Use the graphical wizard to activate your purchasing account." /SD IDOK
    Abort
  InitPluginsDir
  SetOutPath "$PLUGINSDIR\python"
  File /r "../build/windows-python/*"
  SetOutPath "$PLUGINSDIR\src"
  File "../src/mosaicearth_license.py"
  File "../src/mosaicearth_sync.py"
  SetOutPath "$PLUGINSDIR\resources"
  File "../resources/license-public.json"
FunctionEnd

Function AccountPage
  !insertmacro MUI_HEADER_TEXT "Activate your purchasing account" "This installation is associated with your account and this computer."
  nsDialogs::Create 1018
  Pop $AccountDialog
  ${If} $AccountDialog == error
    Abort
  ${EndIf}
  ${NSD_CreateLabel} 0 0 100% 28u "Sign in with the account that purchased Desktop (29 EUR) or has Studio. The installer does not store your password."
  Pop $0
  ${NSD_CreateLabel} 0 40u 100% 12u "Email"
  Pop $0
  ${NSD_CreateText} 0 54u 100% 13u "$EmailValue"
  Pop $EmailField
  ${NSD_CreateLabel} 0 76u 100% 12u "Password"
  Pop $0
  ${NSD_CreatePassword} 0 90u 100% 13u ""
  Pop $PasswordField
  ${NSD_CreateLabel} 0 116u 100% 30u "Internet is required for activation and the initial engine download. Account activation does not change the rights granted by the open-source licenses."
  Pop $0
  nsDialogs::Show
FunctionEnd

Function AccountLeave
  ${NSD_GetText} $EmailField $EmailValue
  ${NSD_GetText} $PasswordField $PasswordValue
  ${If} $EmailValue == ""
  ${OrIf} $PasswordValue == ""
    MessageBox MB_ICONEXCLAMATION "Enter your MosaicEarth email and password."
    Abort
  ${EndIf}
  FileOpen $CredentialsFile "$PLUGINSDIR\activation-input.txt" w
  FileWriteUTF16LE $CredentialsFile "$EmailValue$\r$\n$PasswordValue$\r$\n"
  FileClose $CredentialsFile
  StrCpy $PasswordValue ""
  nsExec::ExecToStack '"$PLUGINSDIR\python\python.exe" "$PLUGINSDIR\src\mosaicearth_license.py" --activate-file "$PLUGINSDIR\activation-input.txt" --data "$LOCALAPPDATA\mosaicearth-desktop"'
  Pop $ActivationResult
  Pop $ActivationOutput
  Delete "$PLUGINSDIR\activation-input.txt"
  ${If} $ActivationResult != "0"
    MessageBox MB_ICONSTOP "$ActivationOutput"
    Abort
  ${EndIf}
  StrCpy $Activated "yes"
FunctionEnd

Section "MosaicEarth Desktop"
  ${If} $Activated != "yes"
    Abort "Account activation is required for this official installer."
  ${EndIf}
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  File "../LICENSE"
  File "../NOTICE"
  File "../build/NSIS-COPYRIGHT"
  File "../THIRD_PARTY_LICENSES.md"
  File "../INSTALL_WINDOWS.md"
  File "../build/SOURCE_REVISION"
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
  CreateShortCut "$SMPROGRAMS\MosaicEarth Desktop\MosaicEarth Desktop.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\src\mosaicearth_desktop.py"'
  CreateShortCut "$DESKTOP\MosaicEarth Desktop.lnk" "$INSTDIR\python\pythonw.exe" '"$INSTDIR\src\mosaicearth_desktop.py"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MosaicEarthDesktop" "DisplayName" "MosaicEarth Desktop"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MosaicEarthDesktop" "DisplayVersion" "0.2.0"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MosaicEarthDesktop" "UninstallString" '"$INSTDIR\Uninstall.exe"'
SectionEnd

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
  Delete "$INSTDIR\SOURCE_REVISION"
  RMDir /r "$INSTDIR\src"
  RMDir /r "$INSTDIR\resources"
  RMDir /r "$INSTDIR\python"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd
