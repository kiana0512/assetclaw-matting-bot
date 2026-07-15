# Quickstart

## 标准启动（飞书长连接，无需公网）

Agent 只使用 conda env `assetclaw`。ComfyUI 后端单独用秋叶启动器环境，不要在 `assetclaw` 里安装或启动 ComfyUI。

```powershell
cd <project-root>
conda activate assetclaw
pip install -r requirements.txt
```

编辑 `.env`（填写 FEISHU_APP_ID、FEISHU_APP_SECRET、DEEPSEEK_API_KEY、SKILL_API_TOKEN）：

```env
FEISHU_EVENT_MODE=ws
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=your_secret
BRAIN_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxxx
DEEPSEEK_ROUTER_MODEL=deepseek-v4-flash
DEEPSEEK_SUMMARY_MODEL=deepseek-v4-pro
SKILL_API_TOKEN=your_token
```

启动：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\start_bot_local.ps1
```

如果要跑真实 ComfyUI 抠图，先单独启动秋叶环境里的 ComfyUI：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\run_comfyui.ps1
```

飞书后台：**事件与回调 → 使用长连接接收事件**（不需要 URL）。

## 本地测试

```powershell
# 初始化 DB
conda run -n assetclaw python -m assetclaw_matting.cli.main init-db

# 单独启动 Gateway
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\start_local_gateway.ps1

# 测试 DeepSeek / WS 配置 / 单元测试
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\test_deepseek_api.ps1
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\test_feishu_ws_config.ps1
conda run -n assetclaw python -m pytest
```

## 常用指令（无飞书闭环测试）

- `看看 E 盘有哪些文件`
- `把 <project-root>\README.md 复制到 <project-root>\storage\README_copy.md`
- `用 <project-root>\storage\batch_inputs 创建一个抠图批次`
- `你会做什么`
- `查看技能列表`
