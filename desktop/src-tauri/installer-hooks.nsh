!include nsDialogs.nsh
!insertmacro MUI_WELCOMEPAGE_INTERFACE

Var SakuraTelemetryCheckbox
Var SakuraTelemetryChoice

!define MUI_WELCOMEPAGE_TITLE "欢迎安装 Sakura"
!define MUI_WELCOMEPAGE_TEXT ""
!define MUI_PAGE_CUSTOMFUNCTION_SHOW SakuraPrivacyShow
!define MUI_PAGE_CUSTOMFUNCTION_LEAVE SakuraPrivacyLeave

Function SakuraPrivacyShow
  ; 默认正文控件覆盖选项区域，即使文字为空也会遮挡新增控件。
  ShowWindow $mui.WelcomePage.Text ${SW_HIDE}
  ${NSD_CreateLabel} 120u 48u 195u 88u "Sakura 可发送运行统计和错误报告，帮助定位故障。报告可能包含本地路径、请求地址和错误片段。$\r$\n$\r$\n取消下方勾选不影响安装和使用，安装后也可在“系统 → 匿名统计”中更改。$\r$\n$\r$\n已有配置的目录会保留原设置。"
  Pop $0
  SetCtlColors $0 000000 FFFFFF
  ${NSD_CreateCheckbox} 120u 140u 195u 16u "发送运行统计和错误报告"
  Pop $SakuraTelemetryCheckbox
  SetCtlColors $SakuraTelemetryCheckbox 000000 FFFFFF
  ; 返回欢迎页时保留本次选择；新安装默认开启。
  ${If} $SakuraTelemetryChoice == ""
    StrCpy $SakuraTelemetryChoice 1
  ${EndIf}
  ${NSD_SetState} $SakuraTelemetryCheckbox $SakuraTelemetryChoice
  ${NSD_CreateLink} 120u 166u 195u 12u "阅读完整隐私政策"
  Pop $0
  ${NSD_OnClick} $0 SakuraPrivacyOpen
FunctionEnd

Function SakuraPrivacyLeave
  ${NSD_GetState} $SakuraTelemetryCheckbox $SakuraTelemetryChoice
FunctionEnd

Function SakuraPrivacyOpen
  Pop $0
  InitPluginsDir
  SetOutPath $PLUGINSDIR
  File /oname=sakura-privacy.txt "${__FILEDIR__}\..\..\docs\guide\privacy.md"
  ExecShell "open" "$PLUGINSDIR\sakura-privacy.txt"
FunctionEnd

!macro NSIS_HOOK_POSTINSTALL
  ; 只为新目录写入初始设置。升级、静默更新和已有配置保持原值。
  ${If} $SakuraTelemetryChoice != ""
  ${AndIf} $UpdateMode <> 1
    IfFileExists "$INSTDIR\config\ui.json" sakura_privacy_done
    CreateDirectory "$INSTDIR\config"
    ClearErrors
    FileOpen $0 "$INSTDIR\config\ui.json" w
    IfErrors sakura_privacy_failed
    ${If} $SakuraTelemetryChoice = ${BST_CHECKED}
      FileWrite $0 '{"schema_version":1,"domain":"ui","settings":{"telemetry":{"enabled":true}}}'
    ${Else}
      FileWrite $0 '{"schema_version":1,"domain":"ui","settings":{"telemetry":{"enabled":false}}}'
    ${EndIf}
    FileClose $0
    IfErrors sakura_privacy_failed
    Goto sakura_privacy_done
    sakura_privacy_failed:
      MessageBox MB_OK|MB_ICONSTOP "无法保存统计设置：$INSTDIR\config\ui.json。请检查目录写入权限后重新安装。"
      Abort
    sakura_privacy_done:
  ${EndIf}
  ; Remove an obsolete backdrop experiment that older incremental builds could
  ; accidentally bundle as an external binary. This also cleans existing installs
  ; during an upgrade without touching user-owned files.
  Delete /REBOOTOK "$INSTDIR\windows_host_backdrop_gate.exe"
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ; Tauri removes the files recorded in the bundle, but Python can create
  ; bytecode caches after installation. Recursively remove only the
  ; installer-owned distribution roots so those generated files do not keep
  ; an otherwise successful uninstall behind. Suppress per-file detail output:
  ; large Python environments contain thousands of small files and updating
  ; the NSIS details list for every one makes deletion needlessly slow.
  DetailPrint "Removing Sakura program files..."
  SetDetailsPrint none
  RMDir /r /REBOOTOK "$INSTDIR\core"
  RMDir /r /REBOOTOK "$INSTDIR\python"
  RMDir /r /REBOOTOK "$INSTDIR\plugins\builtin"
  RMDir /r /REBOOTOK "$INSTDIR\plugins\dependencies"
  Delete /REBOOTOK "$INSTDIR\release-inventory.json"
  SetDetailsPrint lastused

  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode <> 1
    DetailPrint "Removing Sakura user data..."
    SetDetailsPrint none
    RMDir /r /REBOOTOK "$INSTDIR\config"
    RMDir /r /REBOOTOK "$INSTDIR\data"
    RMDir /r /REBOOTOK "$INSTDIR\characters"
    RMDir /r /REBOOTOK "$INSTDIR\plugins\user"
    RMDir /r /REBOOTOK "$INSTDIR\tts"
    RMDir /REBOOTOK "$INSTDIR\plugins"
    RMDir /REBOOTOK "$INSTDIR"
    SetDetailsPrint lastused
  ${EndIf}
!macroend
