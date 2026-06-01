# Win3090 Full Setup

安装 Git、Conda、Python 3.11、cloudflared。项目统一使用 conda env `assetclaw`。

```powershell
cd E:\assetclaw-matting-bot
powershell -ExecutionPolicy Bypass -File scripts\setup_unified_env.ps1
```

ComfyUI 后续安装到 `E:\assetclaw-matting-bot\ComfyUI`，当前默认 `COMFYUI_FAKE_MODE=true`。
