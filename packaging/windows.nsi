Unicode true
Name "MosaicEarth Desktop"
OutFile "../installers/MosaicEarth-Desktop-0.1.2-x64-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\MosaicEarth Desktop"
RequestExecutionLevel user
SetCompressor /SOLID lzma

!include "LogicLib.nsh"
!include "x64.nsh"

Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "MosaicEarth Desktop requires a Windows x64 environment."
    Abort
  ${EndIf}
FunctionEnd

Section "MosaicEarth Desktop"
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
  SetOutPath "$INSTDIR\python"
  File /r "../build/windows-python/*"
  SetOutPath "$INSTDIR"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\MosaicEarth Desktop"
  CreateShortCut "$SMPROGRAMS\MosaicEarth Desktop\MosaicEarth Desktop.lnk" "$INSTDIR\python\python.exe" '"$INSTDIR\src\mosaicearth_desktop.py"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MosaicEarthDesktop" "DisplayName" "MosaicEarth Desktop"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MosaicEarthDesktop" "DisplayVersion" "0.1.2"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MosaicEarthDesktop" "UninstallString" '"$INSTDIR\Uninstall.exe"'
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MosaicEarthDesktop"
  Delete "$SMPROGRAMS\MosaicEarth Desktop\MosaicEarth Desktop.lnk"
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
