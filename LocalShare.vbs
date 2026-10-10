Set objShell = CreateObject("Wscript.Shell")
Set objFSO = CreateObject("Scripting.FileSystemObject")
strDir = objFSO.GetParentFolderName(WScript.ScriptFullName)
strExe = objFSO.BuildPath(strDir, "LocalShare.exe")
strPy = objFSO.BuildPath(strDir, "localshare.py")

' Prefer standalone LocalShare.exe if present (runs on any Windows PC without Python)
If objFSO.FileExists(strExe) Then
    objShell.CurrentDirectory = strDir
    objShell.Run Chr(34) & strExe & Chr(34), 0, False
Else
    ' Fallback to pythonw with 0 console window
    strCmd = "cmd /c cd /d """ & strDir & """ && pythonw """ & strPy & """"
    objShell.Run strCmd, 0, False
End If
