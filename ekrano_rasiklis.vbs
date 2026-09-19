Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
script = dir & "\whiteboard_tool.py"
pyw = "C:\Users\amine\AppData\Local\Programs\Python\Python312\pythonw.exe"
If Not fso.FileExists(pyw) Then pyw = "pythonw.exe"
sh.Run """" & pyw & """ """ & script & """", 0, False
