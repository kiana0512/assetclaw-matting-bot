@echo off
cd /d "%~dp0"

set "FPS=24"
set "DIFF_THRESHOLD=0.2"

start "feishu_frame_tool" pythonw gui.py --fps %FPS% --diff-threshold %DIFF_THRESHOLD%
exit
