@echo off
cd /d "%~dp0"
pwsh -NoProfile -ExecutionPolicy Bypass -File "scripts\stop_webui_public.ps1"
pause
