# Feishu 飞书接入说明

> **安全合规说明**：公司内网部署禁止使用 Cloudflare Tunnel、ngrok、frp、Tailscale、ZeroTier、反向 SSH 等任何内网穿透工具。
> 本项目使用飞书官方长连接（WebSocket）模式，无需公网 IP、无需公网域名、不暴露内网服务。

---

## 接入方式：飞书官方长连接（推荐 / 公司内网唯一允许方式）

### 原理

Win3090 本地进程主动连接飞书开放平台 WebSocket 端点（`wss://open.feishu.cn/...`），
无需将本地服务暴露到公网，无需任何内网穿透工具。

```
Win3090 本地 -> 飞书 WebSocket 端点
  接收 im.message.receive_v1 事件
  -> Brain Router -> Skill Registry
  -> 使用飞书 OpenAPI 回复消息
```

---

## 步骤一：飞书开放平台配置

1. 进入 [飞书开放平台](https://open.feishu.cn/app)，打开你的应用。
2. 在左侧菜单进入 **事件与回调 → 事件配置**。
3. 订阅方式选择：**使用长连接接收事件**（不要选"配置请求网址"）。
4. 不需要填写任何请求地址，不需要 URL 验证。
5. 点击**添加事件**，搜索并添加：`im.message.receive_v1`（接收消息事件）。
6. 在**权限管理**中确认已开通：
   - `im:message` 或 `im:message:receive_v1`（接收消息）
   - `im:message.group_at_msg` （可选，群聊 @机器人）
   - `im:message:create_and_send_msg`（发送消息）
   - 文件上传/下载相关权限（用于接收原视频文件、回传 zip 和处理后的图片附件）
   - 消息表情反应相关权限（可选，用于“进度如何”这类消息上的轻量反馈）
7. 发布版本（应用版本）。

---

## 步骤二：配置 `.env`

```env
# Feishu credentials
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=your_secret_here
FEISHU_VERIFICATION_TOKEN=
FEISHU_ENCRYPT_KEY=

# Event mode: ws (long connection) - no public URL needed
FEISHU_EVENT_MODE=ws
FEISHU_ENABLE_WEBSOCKET=true
FEISHU_ENABLE_WEBHOOK=false

# Access control (optional, semicolon separated)
FEISHU_ADMIN_OPEN_IDS=
FEISHU_ALLOWED_OPEN_IDS=
FEISHU_ALLOWED_CHAT_IDS=
```

> 注意：不要在 `.env` 中泄漏 secret/token/key。
> 不要把 `.env` 提交到 git。

---

## 步骤三：安装依赖

```powershell
cd <project-root>
conda activate assetclaw
pip install -r requirements.txt
```

验证 lark_oapi 已安装：

```powershell
python -c "import lark_oapi; print('lark_oapi OK')"
```

---

## 步骤四：启动本地机器人

```powershell
cd <project-root>
conda activate assetclaw
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start_bot_local.ps1
```

等待终端显示：

```
Feishu websocket: connecting...
Cloudflare: disabled
Event mode: ws
```

连接成功后飞书控制台会显示长连接在线。

---

## 步骤五：验收测试

在飞书私聊机器人发送：

```
你会做什么
查看技能列表
查看权限说明
查看当前系统状态
看看 E 盘有哪些文件
这个路径是否存在？<project-root>\README.md
```

预期：机器人正常回复，无需 Cloudflare，无需公网域名。

---

## 排查：长连接无法连接

1. 检查 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 是否正确。
2. 确认飞书应用事件订阅选择的是"长连接"而非"Webhook URL"。
3. 确认 Win3090 能访问 `open.feishu.cn`（需要出向互联网连接）。
4. 查看 `logs/feishu_ws.log` 获取详细错误。
5. 确认 lark-oapi 已安装：`pip install lark-oapi`。

---

## 旧 Webhook 模式

旧 Webhook 模式需要公网回调 URL。公司内网部署禁止使用任何内网穿透工具，因此本项目主线已移除旧 tunnel 脚本，只保留飞书官方长连接模式。
