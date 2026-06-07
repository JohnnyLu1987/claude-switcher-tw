@echo off
chcp 65001 >nul
rem fcc-server 手動啟動（備援用；平常切換器會自動啟動，不需要這個）
title fcc-server (Free Claude Code) - 關閉視窗=停止 proxy
set "SSL_CERT_FILE="
set "TIKTOKEN_CACHE_DIR=%USERPROFILE%\.fcc\tiktoken-cache"
echo fcc-server 啟動中... 此視窗請勿關閉（關閉=停止 Free Claude Code proxy）
echo.
"%USERPROFILE%\.local\bin\fcc-server.exe"
echo.
echo fcc-server 已停止。按任意鍵關閉。
pause >nul
