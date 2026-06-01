# Operations

## 启动本地安全模式（推荐，公司内网标准方式）

```powershell
cd E:\assetclaw-matting-bot
conda activate assetclaw
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\start_bot_local.ps1
```

启动内容：
1. 后台隐藏启动本地 Gateway（127.0.0.1:7865，仅本地调试，不对外暴露）
2. 后台隐藏启动飞书长连接 WS Receiver（无需公网，无需 Cloudflare）
3. 当前窗口显示系统状态，并持续 tail `logs\conversation.log`

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
```

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
