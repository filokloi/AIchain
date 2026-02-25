' AIchain v4.0 - Ghost Monitor Launcher
' This script launches the AIchain Ghost Watcher in the background.
' It monitors for 429/timeout errors instantly and performs a 4-hour periodic sync.

Set WshShell = CreateObject("WScript.Shell")
scriptPath = WScript.ScriptFullName
scriptDir = Left(scriptPath, InStrRev(scriptPath, "\"))

' Run pythonw aichain.py --watch invisibly (0) and do not wait for return (False)
WshShell.Run "pythonw """ & scriptDir & "aichain.py"" --watch", 0, False
