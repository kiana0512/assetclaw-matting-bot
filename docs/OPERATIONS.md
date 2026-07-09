# Operations

## 一键重启本地机器人（推荐，公司内网标准方式）

```powershell
cd E:\assetclaw-matting-bot
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\start_bot_local.ps1
```

`start_bot_local.ps1` 是后端维护的唯一推荐入口。它会：

1. 停掉旧的 Gateway（端口 `7865`）
2. 停掉旧的 WebUI（端口 `5180`）
3. 停掉旧的飞书 WS Receiver
4. 初始化数据库
5. 后台隐藏启动本地 Gateway（`http://127.0.0.1:7865`）
6. 后台隐藏启动飞书长连接 WS Receiver
7. 后台隐藏启动 WebUI（`http://127.0.0.1:5180`）
8. 当前窗口显示系统状态，并持续 tail `logs\conversation.log`

日常不要手动 kill 端口再逐个启动服务，除非在排查 `start_bot_local.ps1` 本身。

关闭这个日志窗口不会自动停止后台服务。停止服务请运行 `scripts\stop_bot_local.ps1`。

## 停止

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\stop_bot_local.ps1
```

## 单独启动

```powershell
# 仅 Gateway（本地调试）
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\start_local_gateway.ps1

# 仅飞书 WS 接收器
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\start_feishu_ws.ps1

# 仅 WebUI
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\start_external_webui.ps1
```

单独启动只用于定位问题。正常恢复服务用 `start_bot_local.ps1`。

## 清理缓存和运行产物

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\clean_project.ps1
```

清理范围：

- Python 缓存：`__pycache__`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache`
- 临时运行记录：`storage/agent_jobs`、`storage/animation_flow_runner`、`storage/animation_flow_runs`、`storage/custom_pipeline_runs`
- WebUI 临时上传：`storage/webui_uploads`
- 已确认重复的根目录临时文件：`SpriteAtlasGeneratorTool.cs`

保留范围：

- `.env`
- `data/assetclaw.db`
- 非 Cloudflare 的运行日志
- `src/`、`tests/`、`docs/`
- Unity 工程真实资产与 `E:\animation_automation` 业务输出

## 健康检查

```powershell
Invoke-RestMethod http://127.0.0.1:7865/health
```

## 查看日志

```powershell
Get-Content logs\conversation.log -Tail 80 -Wait
Get-Content logs\feishu_ws.log -Tail 50
Get-Content logs\gateway.log -Tail 50
```

## 安全合规说明

**禁止使用**：Cloudflare Tunnel、ngrok、frp、Tailscale、ZeroTier、反向 SSH 或任何内网穿透工具。  
**原因**：公司内网安全合规要求，禁止将内网服务暴露到互联网。  
**替代方案**：飞书官方长连接（WebSocket），本地主动连接飞书，无需任何公网暴露。

## 飞书后台配置（长连接模式）

1. 进入飞书开放平台 → 应用 → 事件与回调 → 事件配置
2. 订阅方式选择：**使用长连接接收事件**（不需要填写 URL）
3. 添加事件：`im.message.receive_v1`
4. 开通：消息接收和发送权限
5. 发布应用版本

## 测试命令

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\test_llm_proxy.ps1
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\test_feishu_ws_config.ps1
conda run -n assetclaw python -m pytest
```

## 独立 Unity 工具

以下能力已经接入飞书 skills，但不属于完整动画自动化 6 步主流程：

- `unity_tools.atlas_status`：读取 `Assets/TATest/AtlasSizeReport.json`
- `unity_tools.atlas_report`：调用 Unity `SpriteAtlasGeneratorTool.DoGenerate()` 生成图集大小报告，需要确认
- `unity_tools.rename_preview`：预览 `AnimTextureBatchRename` 贴图命名整理，不落地
- `unity_tools.rename_run`：执行 `AnimTextureBatchRename`，需要确认

示例飞书指令：

```text
查看图集大小报告
生成图集大小报告
预览动画贴图批量重命名 Assets/Art/UI/SpritesAnim/Emoji/Mia/Common Assets/Art/UI/Animation/Emoji/Mia
执行动画贴图批量重命名 Assets/Art/UI/SpritesAnim/Emoji/Mia/Common Assets/Art/UI/Animation/Emoji/Mia
```
