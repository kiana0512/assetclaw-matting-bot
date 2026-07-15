# Security Policy

## 接入方式安全要求（红线）

**禁止**使用任何内网穿透工具暴露公司内网服务：

- Cloudflare Tunnel / trycloudflare
- ngrok
- frp / frps / frpc
- Tailscale
- ZeroTier
- 反向 SSH 隧道
- 任何将本地服务映射到公网的方法

**原因**：公司内网安全合规要求，禁止绕过网络边界管控。

**正确做法**：使用飞书官方长连接（WebSocket）模式，本地主动连接飞书，无需任何公网暴露。

```
Win3090 -> 出向 wss://open.feishu.cn -> 接收消息 -> 处理 -> 回复
```

---

## 文件系统安全

### 允许路径

- 未设置 `ALLOWED_ROOTS` 时，程序自动允许项目所在盘的根目录。本次部署在 `C:\assetclaw-matting-bot`，因此默认允许 `C:\`。
- 如需共享盘或额外盘符，使用分号追加到 `ALLOWED_ROOTS`；不要把路径写回源码。

### 永久禁止访问

以下路径或关键词命中时，所有 file skill 调用立即拒绝：

| 禁止模式 | 说明 |
|---------|------|
| `.env` | 配置密钥文件 |
| `.ssh` | SSH 密钥目录 |
| `Windows` | 系统目录 |
| `$Recycle.Bin` | 回收站 |
| `System Volume Information` | 系统卷信息 |

### 禁止的操作

- 任意 shell 执行（`ALLOW_SHELL_EXEC=false`，不可覆盖）
- 删除文件（`ALLOW_FILE_DELETE=false`，不可覆盖）
- 读取任意文件内容（`ALLOW_FILE_READ_CONTENT=false`，默认关闭）
- 路径穿越（`../` 会被拒绝）

---

## 网络安全

### Gateway 监听限制

- Gateway 只监听 `127.0.0.1:7865`
- 不监听 `0.0.0.0`（不对任何外部 IP 开放）
- 不作为飞书事件的公网入口

### 飞书事件接收

- 使用长连接（WebSocket），本地主动连接飞书
- 不需要、不允许配置 Webhook URL
- 不生成公网回调地址

---

## 敏感信息保护

- `token`、`secret`、`key`、`password`、`authorization` 在所有日志和 SQLite 审计中自动脱敏
- `.env` 中的任何字段不会出现在飞书回复或日志明文中
- `FEISHU_APP_SECRET` 只在获取 tenant_access_token 时使用，不记录
- `LLM_PROXY_API_KEY` 只在 Brain 请求头中使用，不记录

---

## 权限控制

### 用户权限

配置 `.env` 中以下字段可以限制能调用写操作的用户：

```env
FEISHU_ALLOWED_OPEN_IDS=ou_user1;ou_user2
FEISHU_ALLOWED_CHAT_IDS=oc_chat1;oc_chat2
FEISHU_ADMIN_OPEN_IDS=ou_admin1
```

- 读操作（`readonly` risk_level）默认允许所有用户
- 写操作（`write_safe`、`write_caution`）检查 `FEISHU_ALLOWED_OPEN_IDS`
- 如果未配置，默认允许（宽松模式）

### Skill 风险等级

| 级别 | 说明 | 例子 |
|------|------|------|
| `readonly` | 无写操作，无副作用 | file.exists, bot.help |
| `write_safe` | 写入 DB 或创建文件，低风险 | file.copy, file.mkdir |
| `write_caution` | 移动/重命名，中风险 | file.move |
| `danger_confirm` | 高风险，必须二次确认 | file.delete, file.empty_dir |
| `dangerous_blocked` | 永久禁止，不实现 | shell exec, disk format, partition, C drive access |

---

## 审计

所有 Skill 调用（成功和失败）记录到 SQLite `skill_calls` 表：

```sql
request_id, skill, arguments_json (redacted), result_json (redacted),
ok, error, requested_by, created_at
```

所有飞书消息处理记录到 `brain_messages` 表。

所有事件去重记录到 `feishu_event_dedup` 表（含 trace_id）。

---

## 事件去重

- 同一个 `event_id` 只处理一次
- 同一个 `message_id` 只处理一次（fallback）
- 防止重复执行 `file.copy`、`file.move`、`matting.batch_start` 等写操作

---

## 错误处理安全

- 所有错误消息推送到飞书前必须脱敏
- 不在飞书回复中暴露文件系统路径细节（仅提示方向）
- 所有错误带 `trace_id`，可在日志中追查
