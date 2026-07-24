@echo off
chcp 65001 >nul
title AssetClaw Public WebUI
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\start_webui_public.ps1"
echo.
pause
