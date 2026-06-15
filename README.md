AssetClaw Win3090 Animation Butler 是一个运行在 Windows RTX 3090 服务器上的自动化执行节点。通过飞书官方长连接（WebSocket）接收自然语言指令，由 DeepSeek API + 自建 Brain Router 将自然语言转换成受控 skills 调用，再由 Win3090 本地执行文件管理、ComfyUI 批量抠图等任务，并将结果返回给飞书。

**安全合规**：使用飞书官方长连接模式，无需公网 IP、无需 Cloudflare Tunnel、无需任何内网穿透工具。

## Architecture

- 飞书 = 嘴巴：通过长连接 WebSocket 接收和回复消息。
- DeepSeek + Brain Router = 大脑：把自然语言变成 JSON tool calls。
- DeepSeek v4 = 核心模型：通过 DeepSeek API 调用，不在本地跑 LLM。
- Win3090 = 身体：只运行 Gateway（本地调试）、WS Receiver、Skills、Worker。
- Skills = 四肢：所有机器动作都必须经过 Skill Registry。
- DB / Logs = 记忆：记录 brain message 和 skill audit。
- 多用户隔离：飞书会话按 `chat_id + open_id` 隔离，群内不同用户的上下文和记忆不会混在一起。

## Quick Start

```powershell
cd E:\assetclaw-matting-bot
conda activate assetclaw
pip install -r requirements.txt
```

编辑 `.env`，至少填写：

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_ROUTER_MODEL` / `DEEPSEEK_SUMMARY_MODEL`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_EVENT_MODE=ws`
- `SKILL_API_TOKEN`

一键启动/重启本地机器人（长连接模式，无需公网）：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\start_bot_local.ps1
```

该命令会先停止旧的 Gateway、飞书 WS Receiver、WebUI，再后台启动三件套：

- Gateway：`http://127.0.0.1:7865`
- 飞书 WS Receiver：官方长连接
- WebUI：`http://127.0.0.1:5180`

当前窗口会显示系统状态与 `logs\conversation.log` 实时链路日志。停止服务：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\stop_bot_local.ps1
```

清理缓存/运行产物：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\clean_project.ps1
```

飞书后台配置：**事件与回调 → 事件配置 → 使用长连接接收事件**（无需填写回调 URL）。

## Local Tests

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\test_deepseek_api.ps1
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\test_feishu_ws_config.ps1
conda run -n assetclaw python -m pytest
```

## Feishu 自然语言测试

发送给飞书机器人：

```
你会做什么
查看技能列表
查看权限说明
查看当前系统状态
看看 E 盘有哪些文件
E:\assetclaw-matting-bot\README.md 是否存在
```

## 当前已实现能力

### 文件系统（受控）
- `file.list_allowed` / `file.exists` / `file.info` / `file.find_name` / `file.tree` / `file.recent`
- `file.copy` / `file.move` / `file.mkdir`

### 记忆
- `memory.remember` / `memory.list`

### 抠图批次（fake mode）
- `matting.batch_create/start/status/list/detail/pause/resume/cancel`

### 系统 / 帮助
- `bot.help` / `bot.skills` / `bot.permissions` / `bot.status` / `bot.errors`

### 动画自动化 / Unity
- 完整 7 步主流程：飞书下载 -> 抽帧 -> ComfyUI 抠图 -> Cherry 平滑后处理 -> unity_ready -> Unity 导入 -> P4 shelve。
- unity_ready：`unity_ready.preview/build/status`
- Unity 导入/替换：`unity_import.preview/run/status`
- 独立 Unity 工具：`unity_tools.atlas_status/atlas_report/rename_preview/rename_run`

`unity_tools.*` 是独立能力，不会触发完整 7 步动画自动化流程。

### API（本地调试）
- `GET /health`
- `POST /brain/test`
- `GET /skills/v1/manifest`
- `POST /skills/v1/call`

## 安全边界

- 禁止：任意 shell、删除文件、读取 `.env`、访问系统目录
- 允许路径：`ALLOWED_ROOTS=D:;E:;F:` 下受控操作，`C:` 不开放
- 禁用：Cloudflare Tunnel、ngrok、frp、任何内网穿透工具
- 事件模式：飞书长连接（WebSocket），无公网暴露

## 详细文档

- [飞书接入说明](docs/FEISHU_SETUP.md)
- [运维启动/停止/清理](docs/OPERATIONS.md)
- [技能权限矩阵](docs/SKILL_PERMISSION_MATRIX.md)
- [飞书机器人使用说明](docs/FEISHU_BOT_USAGE.md)
- [动画自动化到 Unity Ready 流程](docs/ANIMATION_UNITY_READY.md)
- [故障排查](docs/TROUBLESHOOTING.md)
