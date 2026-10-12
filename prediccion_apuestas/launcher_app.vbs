' App de Baloncesto - lanzador de inicio automatico (carpeta Inicio).
' Lanza la app con GUI VISIBLE y sin consola (pythonw) y NO duplica la
' instancia si ya esta corriendo (busca la ruta de app.py en las lineas
' de comando de python/pythonw).

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")

py = "E:\pythonw.exe"
If Not fso.FileExists(py) Then py = "pythonw.exe"

carpeta = "D:\App-de-Baloncesto\prediccion_apuestas"
app = carpeta & "\app.py"

' ya hay una instancia de ESTA app corriendo? -> no lanzar otra
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT CommandLine FROM Win32_Process WHERE Name='pythonw.exe' OR Name='python.exe'")
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(LCase(p.CommandLine), LCase(app)) > 0 Then WScript.Quit
    End If
Next

sh.CurrentDirectory = carpeta
sh.Run """" & py & """ """ & app & """", 1, False
