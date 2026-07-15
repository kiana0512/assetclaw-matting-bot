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
cd <project-root>
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
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start_bot_local.ps1
```

该命令会先停止旧的 Gateway、飞书 WS Receiver、WebUI，再后台启动三件套：

- Gateway：`http://127.0.0.1:7865`
- 飞书 WS Receiver：官方长连接
- WebUI：`http://127.0.0.1:5180`

当前窗口会显示系统状态与 `logs\conversation.log` 实时链路日志。停止服务：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\stop_bot_local.ps1
```

清理缓存/运行产物：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\clean_project.ps1
```

飞书后台配置：**事件与回调 → 事件配置 → 使用长连接接收事件**（无需填写回调 URL）。

## Local Tests

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\test_deepseek_api.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\test_feishu_ws_config.ps1
conda run -n assetclaw python -m pytest
```

## Feishu 自然语言测试

发送给飞书机器人：

```
你会做什么
查看技能列表
查看权限说明
查看当前系统状态
看看项目盘有哪些文件
<project-root>\README.md 是否存在
```

## 当前已实现能力

### 文件系统（受控）
- `file.list_allowed` / `file.exists` / `file.info` / `file.find_name` / `file.tree` / `file.recent`
- `file.copy` / `file.move` / `file.mkdir`

### 记忆
- `memory.remember` / `memory.list`

### 抠图批次（fake mode）
- `matting.batch_create/start/status/list/detail/pause/resume/cancel`

### 飞书直传动画处理
- 视频文件：飞书按“文件”发送 `.mp4/.mov` 后，确认执行再启动；流程为原视频 -> OpenCV 抽帧 -> ComfyUI 抠图 -> Cherry HTML 后处理 -> zip 文件回传。
- 图片文件/图片消息：收到后无需确认，直接 ComfyUI 抠图 -> Cherry HTML 后处理 -> 处理结果按文件附件回传，避免飞书压缩。
- 后处理预设：宽高完全相同走正方形 `256x256`，其他比例走长方形 `384x512`；状态回复会带当前预设。
- 抠图管线：默认使用项目同级的 `<project-parent>\imageclip`（公共机为 `C:\imageclip`）管理 `ImageClip.json`、LoRA、`Cherry_lizi` 和 Cherry HTML；启动任务前会确认资源及软链接状态。

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
- 允许路径：默认自动使用项目所在盘的根目录；本次公共机部署即为 `C:\`。可用 `ALLOWED_ROOTS` 覆盖
- 拒绝路径：`.env`、`.ssh`、`Windows`、`$Recycle.Bin`、`System Volume Information`
- 禁用：Cloudflare Tunnel、ngrok、frp、任何内网穿透工具
- 事件模式：飞书长连接（WebSocket），无公网暴露

## 回复策略

- 普通对话保持短回复。
- 上下文自动整理时会提示：`上下文已整理，会继续接着聊。`
- 动画/图片任务只在开始、关键阶段完成、失败、最终回传时主动通知；用户问“进度如何”时，会定位最近的直传图片/视频任务并回复一条精简状态。
- 进度类提问会优先给原消息加飞书表情反应，减少刷屏。

## 详细文档

- [飞书接入说明](docs/FEISHU_SETUP.md)
- [运维启动/停止/清理](docs/OPERATIONS.md)
- [技能权限矩阵](docs/SKILL_PERMISSION_MATRIX.md)
- [飞书机器人使用说明](docs/FEISHU_BOT_USAGE.md)
- [动画自动化到 Unity Ready 流程](docs/ANIMATION_UNITY_READY.md)
- [C 盘公共机迁移与验证](docs/C_DRIVE_MIGRATION.md)
- [故障排查](docs/TROUBLESHOOTING.md)
