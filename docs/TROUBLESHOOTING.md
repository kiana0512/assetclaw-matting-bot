# Troubleshooting

## 飞书无回复

1. 确认飞书后台事件订阅方式为“使用长连接接收事件”，不是“配置请求网址”。
2. 检查 `.env` 中 `FEISHU_EVENT_MODE=ws`，并且 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 已填写。
3. 确认 WS Receiver 正在运行：查看 `logs/feishu_ws.log`，或重新运行 `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\start_feishu_ws.ps1`。
4. 检查 Win3090 能出向访问飞书开放平台：`open.feishu.cn`。
5. 检查 Gateway 是否运行：`Invoke-RestMethod http://127.0.0.1:7865/health`。
6. 运行 `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\test_feishu_ws_config.ps1` 检查本地配置。

## 飞书重复回复

- 已实现事件去重（`feishu_event_dedup` 表），正常不会重复
- 如果重复，优先查看飞书是否重发了不同 `event_id`，或本地 WS Receiver 是否启动了多个进程
- 可查看 `data/assetclaw.db` 中的 `feishu_event_dedup` 表确认

## 飞书图片没有下载到本地

如果日志里出现：

```text
download_message_resource failed: 400 ... code 99991672 ... Access denied
```

这不是 `storage` 目录问题，而是飞书应用缺少消息资源读取权限。需要在飞书开放平台给应用开通以下任意一个权限，并重新发布/重启机器人：

```text
im:message:readonly
im:message.history:readonly
im:message
```

开通后，图片会保存到：

```text
storage\feishu_inbox\日期\会话\
```

## DeepSeek API 401 / 402 / 404 / 429

1. 401：检查 `.env` 中 `DEEPSEEK_API_KEY` 是否有效。
2. 402 / insufficient balance：检查 DeepSeek 平台充值、余额或计费状态。
3. 404：检查 `DEEPSEEK_BASE_URL=https://api.deepseek.com`，不要重复拼 `/v1`；检查模型名是否为 `deepseek-v4-flash` / `deepseek-v4-pro`。
4. 429 / rate limit：稍后重试，或增加重试/backoff。
5. timeout：调大 `DEEPSEEK_TIMEOUT_SECONDS`，或保持 `DEEPSEEK_THINKING_TYPE=disabled`。
6. 运行 `pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\test_deepseek_api.ps1` 直接测试 DeepSeek 和 `/brain/test`。

旧 `scripts\test_llm_proxy.ps1` 仅用于 LLM Proxy 兼容测试。

## Skill token invalid / 401

- 检查请求头 `X-Skill-Token` 是否与 `.env` 中 `SKILL_API_TOKEN` 一致
- `SKILL_API_TOKEN` 默认为 `please_change_me`，上线前必须修改

## JSON decode error / Windows 路径转义

- LLM 返回的 JSON 中 Windows 路径可能有转义问题（`\\` vs `\`）
- Brain 会自动尝试修复一次，仍失败则返回错误
- 测试脚本中路径必须用 PowerShell hashtable + `ConvertTo-Json` 生成，不要手写含反斜杠的 JSON 字符串

## 中文乱码

- 确保 PowerShell 使用 UTF-8：在脚本顶部加：
  ```powershell
  chcp 65001 | Out-Null
  [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
  ```
- 启动 Gateway 时用 `pwsh`（PowerShell 7+），不用 `powershell`（5.x）
- 日志文件编码已设置为 UTF-8

## Cloudflare / 内网穿透（已禁用）

公司内网部署禁止使用 Cloudflare Tunnel、ngrok、frp、Tailscale 等工具。
请使用飞书长连接模式（FEISHU_EVENT_MODE=ws），无需公网 URL。
参考：[飞书接入说明](FEISHU_SETUP.md)

## 路径被拒绝 (PermissionDenied)

- 路径必须在 `ALLOWED_ROOTS`（默认 `D:`、`E:`、`F:`、`Z:`、指定共享盘 UNC）下，`C:` 不开放
- 路径不能包含：`.env`、`.ssh`、`AppData`、`Windows`、`Program Files`、`ProgramData`
- 发送"查看权限说明"可在飞书内查看完整安全边界

## Gateway 启动失败 / 端口占用

```powershell
netstat -ano | findstr :7865
```
如有进程占用 7865 端口，终止后重启 Gateway。

## 数据库锁定

- SQLite 同时多个写操作可能锁定
- 重启 Gateway 通常可以解决
- 如果 `data/assetclaw.db` 损坏，备份后删除，重启会重建 schema

## 飞书验证失败 (403)

- 长连接模式不需要配置请求网址，也不需要 `/feishu/events` URL 验证。
- 如果后台仍显示 URL 验证，说明事件订阅方式选错了，请切换为“使用长连接接收事件”。

## 常用诊断命令

```powershell
# 检查 Gateway 健康
Invoke-RestMethod http://127.0.0.1:7865/health

# 直接调用 brain 测试
$body = @{text="你会做什么"} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:7865/brain/test -Method Post -ContentType "application/json; charset=utf-8" -Body $body

# 检查 skill manifest
$headers = @{"X-Skill-Token"="your_token"}
Invoke-RestMethod http://127.0.0.1:7865/skills/v1/manifest -Headers $headers

# 运行完整测试套件
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\test_llm_proxy.ps1
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\test_deepseek_api.ps1
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts\test_feishu_ws_config.ps1
conda run -n assetclaw python -m pytest
```
