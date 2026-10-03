' Starts the Soren91 agent supervisor with no visible window (window style 0)
' and waits for it, so the scheduled task instance lives as long as the
' supervisor does (MultipleInstances=IgnoreNew then prevents duplicates).
Set shell = CreateObject("WScript.Shell")
dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
cmd = "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & dir & "\soren91_agent_supervisor.ps1"""
WScript.Quit shell.Run(cmd, 0, True)
