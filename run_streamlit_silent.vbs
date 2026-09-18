' Double-click this to launch the Streamlit control panel with NO visible
' command-prompt window (run_streamlit.bat is the same launch with the
' console left visible, if you ever want to watch it directly instead).
' Your browser still opens automatically at http://localhost:8501, same
' as always -- only the console window is suppressed. Output that would
' have gone to that console instead goes to logs\streamlit.log.
'
' There's no window here to close to stop the app -- use
' stop_streamlit.bat for that.
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
CreateObject("WScript.Shell").Run """" & scriptDir & "\run_streamlit_silent.bat""", 0, False
