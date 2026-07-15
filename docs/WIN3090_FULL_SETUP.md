# Win3090 Full Setup

安装 Git、Conda、Python 3.11。Agent 统一使用 conda env `assetclaw`。

```powershell
cd <project-root>
powershell -ExecutionPolicy Bypass -File scripts\setup_unified_env.ps1
```

## 环境分工

不要把两个 Python 环境混在一起：

- `assetclaw`：Agent 主环境。运行 Gateway、飞书长连接、Brain Router、Skills、ASR/TTS、P4、测试脚本。
- 秋叶 ComfyUI：跑图环境。只用于启动 `<comfyui-root>\ComfyUI` 和需要秋叶依赖的 Cherry worker。

当前 ComfyUI 配置：

```text
COMFYUI_AKI_ROOT=<comfyui-root>
COMFYUI_DIR=<comfyui-root>\ComfyUI
COMFYUI_PYTHON_DIR=<comfyui-root>\python
COMFYUI_URL=http://127.0.0.1:8188
```

启动 ComfyUI 后端：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\run_comfyui.ps1
```

启动 Agent：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start_bot_local.ps1
```
