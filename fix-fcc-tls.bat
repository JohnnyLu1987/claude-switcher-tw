@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ============================================
echo   fix-fcc-tls：修復 fcc 連線（TLS 攔截）
echo ============================================
echo.
echo 在 fcc 的虛擬環境裝 truststore，並加 .pth 自動注入，
echo 讓 fcc 改用 Windows 憑證驗證（解決防毒/防火牆掃 HTTPS 導致連不上模型）。
echo.

REM ---------- 1. 確認 uv ----------
uv --version >nul 2>&1
if errorlevel 1 (
  echo [失敗] 找不到 uv。fcc 是用 uv tool 安裝的，請先確認 uv 已安裝並在 PATH。
  pause & exit /b 1
)

REM ---------- 2. 定位 fcc venv ----------
set "UVTOOLS="
for /f "delims=" %%i in ('uv tool dir 2^>nul') do set "UVTOOLS=%%i"
set "VENV=%UVTOOLS%\free-claude-code"
if not exist "%VENV%" (
  echo [失敗] 找不到 fcc venv：%VENV%
  echo 請先安裝 fcc，例如：
  echo   uv tool install "git+https://github.com/Alishahryar1/free-claude-code.git" --python 3.14.5 --system-certs
  pause & exit /b 1
)
set "VPY=%VENV%\Scripts\python.exe"
if not exist "%VPY%" (
  echo [失敗] 找不到 venv 的 python：%VPY%
  pause & exit /b 1
)
echo 找到 fcc venv：%VENV%
echo.

REM ---------- 3. 安裝 truststore ----------
echo [1/3] 在 fcc venv 安裝 truststore...
uv pip install --python "%VPY%" truststore
if errorlevel 1 (
  echo [失敗] truststore 安裝失敗，請把上面訊息貼給 Claude。
  pause & exit /b 1
)
echo.

REM ---------- 4. 寫入自動注入 .pth ----------
echo [2/3] 建立自動注入檔 zz_truststore_inject.pth...
set "PTH=%VENV%\Lib\site-packages\zz_truststore_inject.pth"
powershell -NoProfile -Command "Set-Content -LiteralPath $env:PTH -Value 'import truststore; truststore.inject_into_ssl()' -Encoding ascii"
if not exist "%PTH%" (
  echo [失敗] .pth 檔建立失敗：%PTH%
  pause & exit /b 1
)
echo        已寫入：%PTH%
echo.

REM ---------- 5. 驗證 ----------
echo [3/3] 驗證注入是否生效...
"%VPY%" -c "import truststore; truststore.inject_into_ssl(); print('  truststore 注入 OK')"
if errorlevel 1 (
  echo [注意] 注入驗證失敗，請把上面訊息貼給 Claude。
  pause & exit /b 1
)
echo.

echo ============================================
echo   [完成] TLS 修復完成！
echo ============================================
echo   請重啟 fcc-server 生效（切換器切到 Free 時會自動拉起，
echo   或雙擊「啟動fcc-server.bat」）。
echo.
pause
