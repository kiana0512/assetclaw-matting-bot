# Feishu Setup Guide

## 1. Create a Feishu App

1. Go to https://open.feishu.cn/app
2. Create a new app (企业自建应用)
3. Note your App ID and App Secret → add to `.env`

## 2. Enable Required Capabilities

In the app console:
- IM → Enable (机器人)

## 3. Required Permissions

| Permission | Why |
|-----------|-----|
| im:message:readonly (读取单聊消息) | Receive user messages |
| im:message (以机器人身份发送消息) | Reply to users |
| im:message.group_at_msg:readonly (读取群聊 @ 消息) | Receive @bot in groups |

## 4. Subscribe to Events

Events → Add event subscription:
- `im.message.receive_v1`

Callback URL: `https://<your-cloudflared-host>/feishu/events`

Set Verification Token → add to `.env` as `FEISHU_VERIFICATION_TOKEN`

## 5. Start cloudflared

```powershell
cloudflared tunnel --url http://127.0.0.1:7865
# Copy the https://xxxx.trycloudflare.com URL
```

Update Feishu callback URL with the new address whenever cloudflared restarts.

## 6. Publish the App

In the app console → Version Management → Create version → Submit for review
(or self-approve for enterprise apps)

## 7. Supported Commands

| Command | Response |
|---------|----------|
| `help` | Command list |
| `queue` | Current queue stats |
| `batch list` | Recent 10 batches |
| `batch status <id>` | Batch progress |
| `batch cancel <id>` | Cancel queued tasks |
| `task status <id>` | Task details |
| Any other text | Forwarded to OpenClaw (if enabled) or "OpenClaw not enabled" |

## 8. Notification Chat ID

For batch progress notifications, set `notify_chat_id` when creating a batch.
The bot will send progress updates to that group/chat.

To find a chat_id: use Feishu API `GET /im/v1/chats` or inspect event payloads.
