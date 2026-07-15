# C 盘公共机迁移与验证

本次公共机目录：

- 项目：`C:\assetclaw-matting-bot`
- 动画工作区：`C:\animation_auto`
- 抠图/后处理管线：`C:\imageclip`

这两个值不写入业务源码。程序从 `config.py` 的实际位置推导项目根目录，并把同级的 `animation_auto` 作为默认动画根目录。将来换盘或换父目录时，正常情况下无需改代码。

## 1. 放置代码与创建目录

```powershell
Set-Location C:\assetclaw-matting-bot
New-Item -ItemType Directory -Force C:\animation_auto | Out-Null
Copy-Item .env.example .env
```

只在 `.env` 填写密钥和机器差异项。不要重新加入旧机器盘符。以下路径项均可省略，让程序自动推导：

- `ASSETCLAW_ROOT`
- `ANIMATION_ROOT`
- `DATA_DIR` / `STORAGE_DIR` / `LOG_DIR`
- `BOT_STICKER_DIR`

如果目录布局不同，才在 `.env` 覆盖，例如：

```dotenv
ANIMATION_ROOT=C:\animation_auto
COMFYUI_AKI_ROOT=C:\ComfyUI-aki-v3
UNITY_PROJECT_DIR=C:\UnityProject
```

## 2. 权限配置

项目在 C 盘时，`ALLOWED_ROOTS` 默认自动解析为 `C:\`，无需填写。安全层仍拒绝：

- `C:\Windows`
- `.env`、`.ssh`
- `$Recycle.Bin`
- `System Volume Information`

如果还要访问共享盘，在 `.env` 中用分号追加实际路径；不要在 Python 或 PowerShell 中写死：

```dotenv
ALLOWED_ROOTS=C:\;Z:\;\\server\share
```

## 3. 安装环境

推荐 Python 3.11 或 3.12：

```powershell
Set-Location C:\assetclaw-matting-bot
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .
```

`requirements.txt` 已包含 OpenCV（导入名 `cv2`）、NumPy、PyTorch、Flask、PyYAML、psutil 和 tqdm。GPU 机器若需要指定 CUDA 版 PyTorch，应先按机器 CUDA 版本安装对应 wheel，再执行 requirements；pip 会复用已满足的版本。

## 4. 配置外部组件

外部组件默认使用项目父目录下的可迁移约定目录：

- ComfyUI：优先使用 `<project-parent>\ComfyUI-aki-v3`；如果秋叶目录名称不同，会自动查找同时包含 `ComfyUI` 和 `python` 子目录的同级目录
- 抠图管线：`<project-parent>\imageclip`
- Unity：`<project-parent>\UnityProject`

目录不同就只改 `.env` 中的 `COMFYUI_*`、`CHERRY_POSTPROCESS_HTML_PATH`、`MATTING_PIPELINE_REPO_DIR`、`UNITY_PROJECT_DIR`。

Unity 项目同样先使用 `<project-parent>\UnityProject`，不存在时会按 `Assets` + `ProjectSettings` 目录结构自动发现。自动发现只有一个候选时最稳妥；公共机存在多套秋叶或 Unity 工程时，请在 `.env` 显式指定。

`imageclip` 目录应包含以下管线资源：

- `ImageClip.json`
- `Koutu_Flux2klein_v2_000007250.safetensors`
- `Cherry_lizi`
- `cherry-postprocess.html`

机器人执行 `matting_pipeline.update` / “更新抠图管线”时，会优先把前三项以软链接同步到秋叶 ComfyUI 的工作流、LoRA 和 `custom_nodes` 目录；系统不允许创建软链接时自动复制。`cherry-postprocess.html` 直接从 `C:\imageclip` 使用，不需要链接到 ComfyUI。

`cherry-postprocess.html` 是后处理的唯一算法依据：256×256 与 384×512 分别使用 HTML 内的不同预设。机器人不会使用 Python 近似算法兜底；因此部署前必须确保该 HTML 和 Chrome/Edge 均可用。

## 5. 验证

```powershell
.\.venv\Scripts\python.exe -c "import cv2, numpy, torch; print(cv2.__version__)"
.\.venv\Scripts\python.exe -c "from assetclaw_matting.config import settings; print(settings.assetclaw_root); print(settings.animation_root); print(settings.allowed_roots_list)"
.\.venv\Scripts\python.exe scripts\public_machine_preflight.py
.\.venv\Scripts\python.exe -m pytest --ignore=tests/test_p4_assistant.py --basetemp=.pytest-tmp
```

预期第二条命令依次包含：

```text
C:\assetclaw-matting-bot
C:\animation_auto
['C:\\']
```

最后执行初始化与健康检查：

```powershell
.\.venv\Scripts\python.exe -m assetclaw_matting.cli.main init-db
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\start_local_gateway.ps1
Invoke-RestMethod http://127.0.0.1:7865/health
```

所有 `scripts/*.ps1` 均从 `$PSScriptRoot` 推导项目目录，可以从任意当前目录调用。
