' 在背景啟動 Claude 模式切換器（不會跳出黑色視窗）
' 啟動後會在右下角系統匣出現一個圓點圖示：藍=原版訂閱，綠=Free Claude Code
' 路徑自我定位：以本檔所在資料夾為準，pythonw 走系統 PATH（不再寫死絕對路徑）。
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = folder
sh.Run "pythonw.exe """ & folder & "\claude_switcher.py""", 0, False
