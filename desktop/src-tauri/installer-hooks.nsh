; Runtime v2 stores its Windows user root beside sakura.exe. Tauri's built-in
; "Delete the application data" checkbox only removes AppData directories named
; after the bundle identifier, so explicitly remove Sakura-owned user-root
; directories when that checkbox is selected. Keep unknown files in a custom
; install directory intact, and never remove user data during an updater run.
!macro NSIS_HOOK_POSTINSTALL
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
