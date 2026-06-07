@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
set "HERE=%~dp0"
set "WORKDIR=%HERE:~0,-1%"

echo ============================================
echo   Claude 模式切換器 - 一鍵安裝
echo ============================================
echo.

REM ---------- 1. 找 Python ----------
set "PY="
py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
if not defined PY (
  python -c "import sys" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo [失敗] 找不到 Python。請先安裝 Python 3.12 並勾選「Add to PATH」。
  echo        下載： https://www.python.org/downloads/
  pause & exit /b 1
)
echo [1/4] 使用 Python：%PY%
echo.

REM ---------- 2. 安裝依賴套件 ----------
echo [2/4] 安裝套件 (pystray / pillow / psutil)...
%PY% -m pip install --upgrade pystray pillow psutil
if errorlevel 1 (
  echo [失敗] 套件安裝出錯，請把上面訊息貼給 Claude。
  pause & exit /b 1
)
echo.

REM ---------- 3. 產生 config.json 並檢查 fcc-server ----------
echo [3/4] 產生設定檔 config.json（自動偵測路徑）...
%PY% -c "import sys; sys.path.insert(0, r'%WORKDIR%'); import claude_switcher as c; print('  fcc-server :', c.FCC_SERVER_EXE); print('  存在?      :', __import__('os').path.isfile(c.FCC_SERVER_EXE)); print('  .env       :', c.FCC_ENV_PATH); print('  port       :', c.PORT)"
if errorlevel 1 (
  echo [失敗] 設定檔產生失敗。
  pause & exit /b 1
)
echo        設定檔位置：%WORKDIR%\config.json
echo        （若 fcc-server「存在?」為 False，請開 config.json 手動修正 fcc_server_exe）
echo.

REM ---------- 4. 建立開機自啟捷徑 ----------
echo [4/4] 建立開機自啟捷徑（shell:startup）...
for /f "delims=" %%i in ('%PY% -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYW=%%i"
if not exist "%PYW%" (
  echo [注意] 找不到 pythonw.exe，捷徑改用 python.exe（啟動時會閃一下視窗）。
  for /f "delims=" %%i in ('%PY% -c "import sys;print(sys.executable)"') do set "PYW=%%i"
)
set "LNKTARGET=%PYW%"
set LNKARGS="%WORKDIR%\claude_switcher.py"
powershell -NoProfile -Command "$ws=New-Object -ComObject WScript.Shell; $p=Join-Path ([Environment]::GetFolderPath('Startup')) 'Claude 模式切換器.lnk'; $sc=$ws.CreateShortcut($p); $sc.TargetPath=$env:LNKTARGET; $sc.Arguments=$env:LNKARGS; $sc.WorkingDirectory=$env:WORKDIR; $sc.Save(); Write-Host ('  捷徑：' + $p)"
if errorlevel 1 (
  echo [注意] 捷徑建立失敗，可改用手動方式（雙擊 2-啟動切換器.vbs）。
)
echo.

echo ============================================
echo   [完成] 安裝成功！
echo ============================================
echo   - 開機會自動啟動，右下角出現圓點圖示
echo   - 想立刻啟動：雙擊「2-啟動切換器.vbs」
echo.
pause
