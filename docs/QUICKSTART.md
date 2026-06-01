# Quickstart

## 标准启动（飞书长连接，无需公网）

```powershell
cd E:\assetclaw-matting-bot
conda activate assetclaw
pip install -r requirements.txt
```

编辑 `.env`（填写 FEISHU_APP_ID、FEISHU_APP_SECRET、LLM_PROXY_API_KEY、SKILL_API_TOKEN）：

```env
FEISHU_EVENT_MODE=ws
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=your_secret
LLM_PROXY_API_KEY=your_key
SKILL_API_TOKEN=your_token
```

启动：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\start_bot_local.ps1
```

飞书后台：**事件与回调 → 使用长连接接收事件**（不需要 URL）。

## 本地测试

```powershell
# 初始化 DB
conda run -n assetclaw python -m assetclaw_matting.cli.main init-db

# 单独启动 Gateway
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\start_local_gateway.ps1

# 测试 LLM / WS 配置 / 单元测试
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\test_llm_proxy.ps1
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\test_feishu_ws_config.ps1
conda run -n assetclaw python -m pytest
```

## 常用指令（无飞书闭环测试）

- `看看 E 盘有哪些文件`
- `把 E:\assetclaw-matting-bot\README.md 复制到 E:\assetclaw-matting-bot\storage\README_copy.md`
- `用 E:\assetclaw-matting-bot\storage\batch_inputs 创建一个抠图批次`
- `你会做什么`
- `查看技能列表`
