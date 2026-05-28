Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

root = fso.GetParentFolderName(WScript.ScriptFullName)
command = "cmd /c cd /d """ & root & """ && "

If fso.FileExists(root & "\.venv\Scripts\pythonw.exe") Then
    command = command & """" & root & "\.venv\Scripts\pythonw.exe"" """ & root & "\setup_wizard.py"" --install --force"
Else
    command = command & "pythonw """ & root & "\setup_wizard.py"" --install --force"
End If

shell.Run command, 0, False
