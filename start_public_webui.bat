@echo off
title AssetClaw WebUI Public URL
cd /d "%~dp0"
echo Starting Cloudflare tunnel for http://127.0.0.1:5180 ...
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "scripts\start_webui_public.ps1"
echo.
echo Keep this window output for the public URL. The URL is also copied to clipboard.
pause
