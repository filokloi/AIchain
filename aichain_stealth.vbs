' ═══════════════════════════════════════════════════
' AIchain Ghost Watcher — Stealth Launcher v4.0
' Place in Startup folder or schedule via Task Scheduler
' ═══════════════════════════════════════════════════

Dim WshShell
Set WshShell = CreateObject("WScript.Shell")

Dim pythonExe
pythonExe = "python"

Dim bridgeScript
bridgeScript = WshShell.ExpandEnvironmentStrings("%USERPROFILE%") & _
    "\OneDrive\Desktop\AI chain for Open Claw envirement\aichain_bridge.py"

' Routing table URL (update after GitHub Pages deployment)
Dim routingUrl
routingUrl = "https://<your-username>.github.io/AIchain/ai_routing_table.json"

' Launch Ghost Watcher in hidden mode (--watch for continuous monitoring)
Dim command
command = """" & pythonExe & """ """ & bridgeScript & """ --watch --url """ & routingUrl & """"

' 0 = hidden window, False = don't wait for completion
WshShell.Run command, 0, False

Set WshShell = Nothing
