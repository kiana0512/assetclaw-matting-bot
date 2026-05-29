# AssetClaw Win3090 Skill Node

**飞书是嘴巴，Brain Router 是大脑，Skill Gateway 是神经接口，Worker 是手脚，ComfyUI 是 GPU 执行器。**

3090 显存只留给 ComfyUI 图像任务，不跑任何本地大模型。AI 推理走云端 API。

---

## 目录

1. [最小闭环：飞书 → Brain → File Skills](#最小闭环飞书--brain--file-skills)
2. [项目结构](#项目结构)
3. [快速开始](#快速开始)
3. [配置说明](#配置说明env)
4. [批量抠图完整流程](#批量抠图完整流程)
5. [Skill API 使用](#skill-api-使用)
6. [Brain Router 配置](#brain-router-配置)
7. [飞书接入](#飞书接入)
8. [日志和调试](#日志和调试)
9. [新增 Skill](#新增-skill)
10. [Admin API 速查](#admin-api-速查)
11. [CLI 命令速查](#cli-命令速查)
12. [常见问题](#常见问题)

---

---

## 最小闭环：飞书 → Brain → File Skills

> 目标：飞书里说"看看 E 盘有哪些文件"或"把 A 复制到 B"，机器人直接响应。

### 第一步：配置 .env

```powershell
Copy-Item .env.example .env
# 用编辑器打开 .env，至少填写以下字段：
```

必填字段：

| 字段 | 说明 |
|------|------|
| `LLM_PROXY_API_KEY` | 公司 LLM Proxy key（Atlas Helper 申请） |
| `LLM_PROXY_MODEL` | 模型名，如 `claude-sonnet-4-6` |
| `FEISHU_APP_ID` | 飞书开放平台 → 应用 → 凭证与基础信息 |
| `FEISHU_APP_SECRET` | 同上 |
| `FEISHU_VERIFICATION_TOKEN` | 飞书 → 事件与回调 → 验证 Token |
| `SKILL_API_TOKEN` | 随机字符串（`python -c "import secrets; print(secrets.token_hex(24))"` 生成） |

### 第二步：初始化并启动 Gateway

```powershell
conda activate assetclaw
python -m assetclaw_matting.cli.main init-db

# 启动 Gateway（保持此窗口开着）
powershell -ExecutionPolicy Bypass -File scripts\run_gateway.ps1
```

验证 Gateway 正常：

```powershell
curl http://127.0.0.1:7865/health
```

### 第三步：启动 cloudflared 获取公网 URL

新开一个 PowerShell 窗口：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\expose_gateway_cloudflared.ps1
```

脚本会自动：
- 启动 `cloudflared tunnel --url http://127.0.0.1:7865`
- 捕获生成的 `https://xxxx.trycloudflare.com` URL
- 写入 `.env` 的 `PUBLIC_BASE_URL`
- 写入 `logs\public_url.txt`
- 打印飞书回调地址并复制到剪贴板

输出示例：

```
==============================================================
  AssetClaw Gateway 隧道已建立
==============================================================

  Public URL:
  https://abc-def-123.trycloudflare.com

  飞书开放平台 > 开发配置 > 事件与回调 > 请求地址：
  https://abc-def-123.trycloudflare.com/feishu/events

  [已复制到剪贴板]
==============================================================
```

> **注意：cloudflared quick tunnel 每次重启 URL 都会变化。**
> 重启后必须重新在飞书后台填写新的回调地址。

### 第四步：填写飞书开放平台

1. 打开[飞书开放平台](https://open.feishu.cn/app)→ 你的应用 → **开发配置 → 事件与回调**
2. 事件配置 → 请求地址 → 填入：
   ```
   https://xxxx.trycloudflare.com/feishu/events
   ```
3. 点"验证"（脚本已配置 `url_verification` 响应，应立即通过）
4. 添加事件：`im.message.receive_v1`
5. 版本管理 → 创建版本 → 发布（企业自建应用自审）

### 第五步：本地验证

```powershell
# 健康检查（包含 brain test、skill manifest、public URL）
powershell -ExecutionPolicy Bypass -File scripts\health_check.ps1

# 测试 file.list_allowed（列目录）
curl -X POST http://127.0.0.1:7865/skills/v1/call `
  -H "Content-Type: application/json" `
  -H "X-Skill-Token: <your_token>" `
  -d '{"skill":"file.list_allowed","arguments":{"path":"E:\\","max_items":20}}'

# 测试 file.copy（复制文件）
curl -X POST http://127.0.0.1:7865/skills/v1/call `
  -H "Content-Type: application/json" `
  -H "X-Skill-Token: <your_token>" `
  -d '{"skill":"file.copy","arguments":{"src_path":"E:\\assetclaw-matting-bot\\README.md","dst_path":"E:\\assetclaw-matting-bot\\storage\\README_copy.md","overwrite":true}}'

# 测试 Brain（不经过飞书，直接测 LLM Proxy -> skills）
curl -X POST http://127.0.0.1:7865/brain/test `
  -H "Content-Type: application/json" `
  -d '{"text":"看看 E 盘有哪些文件"}'
```

### 第六步：飞书测试话术

发这些消息给机器人：

```
看看 E 盘有哪些文件
```

```
把 E:\assetclaw-matting-bot\README.md 复制到 E:\assetclaw-matting-bot\storage\README_copy.md
```

---

## 项目结构

```
src/assetclaw_matting/
├── brain/          可插拔大脑路由（llm_proxy / arkclaw / claude / local_command）
├── skills/         Skill Gateway：所有受控能力
├── comfyui/        ComfyUI HTTP 客户端
├── worker/         Worker 轮询执行循环
├── feishu/         飞书消息入口
├── arkclaw/        ArkClaw 企业版适配层（legacy bridge，现在通过 brain/arkclaw_brain.py）
├── mcp_server/     MCP 兼容层（/mcp/*，供 Claude/Cursor 调用）
├── api/            FastAPI 路由（admin / worker / skills / feishu / arkclaw / mcp）
├── db/             SQLite 数据访问层
├── services/       业务逻辑（batch_service, task_service, notification）
├── models/         Pydantic 数据模型
└── cli/            命令行入口

data/assetclaw.db   SQLite 数据库（batches/tasks/skill_calls/brain_messages）
storage/            任务文件、批次文件、调试文件
logs/               gateway.log / worker.log
workflows/          ComfyUI workflow API JSON（放在这里）
skills_pack/        供 AI Brain 学习的技能文档（喂给 ArkClaw/Claude 的知识库）
```

---

## 快速开始

### 第一步：创建统一 conda 环境

**所有组件（Gateway / Worker / ComfyUI）共用同一个环境，叫 `assetclaw`。**

```powershell
conda create -n assetclaw python=3.11 -y
conda activate assetclaw
pip install -r requirements.txt
```

### 第二步：复制并填写配置

```powershell
Copy-Item .env.example .env
# 用编辑器打开 .env，至少填写：
#   WORKER_TOKEN=（随机字符串）
#   SKILL_API_TOKEN=（随机字符串）
#   Brain 相关（见下方配置说明）
```

### 第三步：初始化数据库

```powershell
python -m assetclaw_matting.cli.main init-db
```

### 第四步：启动 Gateway

```powershell
python -m assetclaw_matting.cli.main gateway
# 看到 "AssetClaw Win3090 Skill Node started" 即成功
```

### 第五步：验证

```powershell
# 基础健康检查
curl http://127.0.0.1:7865/health

# Skill API 清单（用你在 .env 里设置的 token）
curl -H "X-Skill-Token: please_change_me" http://127.0.0.1:7865/skills/v1/manifest
```

---

## 配置说明（.env）

复制 `.env.example` 到 `.env`。下面按优先级说明每个部分。

### ① 必填：鉴权 Token

```env
# Worker 和 Skill API 的鉴权 Token，建议改成随机字符串（两个可以不同）
WORKER_TOKEN=换成随机字符串_不要用默认值
SKILL_API_TOKEN=换成另一个随机字符串
```

生成随机 token：
```powershell
python -c "import secrets; print(secrets.token_hex(24))"
```

### ② 选填：Brain Router（选一个填）

**选项 A：LLM Proxy（公司代理，推荐）**
```env
BRAIN_PROVIDER=llm_proxy
LLM_PROXY_ENABLED=true
LLM_PROXY_BASE_URL=https://your-company-llm-proxy.com/v1
LLM_PROXY_API_KEY=sk-xxxxxxxxxxxx
LLM_PROXY_MODEL=gpt-4o-mini        # 或者你们内部的模型名
LLM_PROXY_TIMEOUT_SECONDS=60
```

**选项 B：只用本地命令（开发/测试阶段，不需要 API Key）**
```env
BRAIN_PROVIDER=local_command
```

**选项 C：ArkClaw 企业版**
```env
BRAIN_PROVIDER=arkclaw
ARKCLAW_ENABLED=true
ARKCLAW_BASE_URL=https://arkclaw.yourcompany.com
ARKCLAW_API_KEY=你的key
ARKCLAW_BOT_ID=你的bot_id
ARKCLAW_WORKSPACE_ID=你的workspace_id
```

**选项 D：Anthropic Claude**
```env
BRAIN_PROVIDER=claude
CLAUDE_BRAIN_ENABLED=true
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxx
CLAUDE_MODEL=claude-sonnet-4-5
```

> Brain 不可用时会自动 fallback 到 `local_command`，不会崩溃。

### ③ 选填：飞书 Bot

```env
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxxx    # 飞书开放平台 → 应用 → 凭证与基础信息
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxx  # 同上
FEISHU_VERIFICATION_TOKEN=xxxxxxxxxxxx # 应用 → 事件与回调 → 验证 token
FEISHU_DEFAULT_NOTIFY_CHAT_ID=         # 可选，批次通知默认发到这个群
```

### ④ 必填：ComfyUI

```env
# 先用 true 测试管道（Pillow mock，不需要 GPU，不需要安装 ComfyUI）
# 跑通后改成 false
COMFYUI_FAKE_MODE=true

COMFYUI_URL=http://127.0.0.1:8188
COMFYUI_WORKFLOW_PATH=E:\assetclaw-matting-bot\workflows\matting_api.json
COMFYUI_TIMEOUT_SECONDS=600
```

### ⑤ 路径权限

```env
# 当前配置允许整个 E 盘
ALLOWED_ROOTS=E:

# 以下路径无论如何都会被拒绝（区分大小写，子字符串匹配）
DENY_PATH_PATTERNS=.ssh;.env;AppData;Windows;Program Files;ProgramData;$Recycle.Bin;System Volume Information
```

如需限制范围（更安全，推荐生产环境）：
```env
ALLOWED_ROOTS=E:\assetclaw-matting-bot;E:\your_project_folder
```

> **注意**：`.env` 文件本身通过 `DENY_PATH_PATTERNS` 中的 `.env` 被拦截，不会被 file.list_allowed 读取。

### ⑥ 其他默认值（通常不用改）

```env
GATEWAY_HOST=127.0.0.1
GATEWAY_PORT=7865
WORKER_ID=win3090-worker-01
AGENT_RUNS_ON_GPU=false   # 永远不要改成 true，GPU 留给 ComfyUI
GPU_TASK_CONCURRENCY=1
WORKER_POLL_INTERVAL_SECONDS=3
```

---

## 批量抠图完整流程

### Fake Mode（无 GPU，验证管道）

> **目的：** 验证飞书 → Gateway → Worker → 输出 全链路能通，不需要真实 ComfyUI。

确认 `.env` 中 `COMFYUI_FAKE_MODE=true`，然后：

```powershell
# 准备：在 batch_inputs 里放几张图片（png/jpg/webp）
Copy-Item "C:\some\photo.jpg" "E:\assetclaw-matting-bot\storage\batch_inputs\"

# ── 终端 1：启动 Gateway ──
conda activate assetclaw
python -m assetclaw_matting.cli.main gateway

# ── 终端 2：创建并启动批次 ──
conda activate assetclaw

# 创建批次（记录输出的 BATCH_XXXX ID）
python -m assetclaw_matting.cli.main batch-create `
    --input-dir  "E:\assetclaw-matting-bot\storage\batch_inputs" `
    --output-dir "E:\assetclaw-matting-bot\storage\batch_outputs" `
    --workflow-type matting_v1

# 启动批次（把上面的 BATCH_XXXX 替换进来）
python -m assetclaw_matting.cli.main batch-start --batch-id BATCH_XXXXXXXXXXXX

# ── 终端 3：启动 Worker ──
conda activate assetclaw
python -m assetclaw_matting.cli.main worker
```

**预期结果：**
- `storage/batch_outputs/photo_matting.png` 出现
- `batch-status --batch-id BATCH_XXX` 显示 `SUCCEEDED`
- `logs/worker.log` 有处理记录

### Real ComfyUI

1. 安装 ComfyUI（见 `docs/WIN3090_FULL_SETUP.md`）
2. 通过 ComfyUI Manager 安装 RMBG / BiRefNet 节点和模型
3. 导出 workflow：
   - ComfyUI → Settings → 勾选 **Enable Dev Mode Options**
   - 搭好抠图工作流（必须有 **LoadImage** 和 **SaveImage** 节点）
   - 点击 **Save (API Format)**（注意不是普通 Save）
   - 保存为 `E:\assetclaw-matting-bot\workflows\matting_api.json`
4. `.env` 改成 `COMFYUI_FAKE_MODE=false`
5. 启动 ComfyUI：
   ```powershell
   conda activate assetclaw
   cd E:\assetclaw-matting-bot\ComfyUI
   python main.py --listen 127.0.0.1 --port 8188
   ```
6. 按 Fake Mode 流程创建批次并启动 Worker

---

## Skill API 使用

所有 `/skills/v1/*` 接口需要 Header：`X-Skill-Token: <SKILL_API_TOKEN>`

### 获取技能清单

```bash
GET /skills/v1/manifest

curl -H "X-Skill-Token: please_change_me" \
     http://127.0.0.1:7865/skills/v1/manifest
```

返回节点信息、所有技能名称和描述。

### 调用技能

```bash
POST /skills/v1/call
Content-Type: application/json
X-Skill-Token: please_change_me

# 查询队列
{"skill": "queue.status", "arguments": {}}

# 创建批次
{
  "skill": "batch.create",
  "arguments": {
    "input_dir": "E:\\batch_inputs\\project01",
    "output_dir": "E:\\batch_outputs\\project01",
    "workflow_type": "matting_v1",
    "notify_chat_id": "oc_xxxx"
  },
  "request_id": "my-req-001",
  "requested_by": "my-brain"
}

# 启动批次
{"skill": "batch.start", "arguments": {"batch_id": "BATCH_XXXX"}}

# 查看批次进度
{"skill": "batch.status", "arguments": {"batch_id": "BATCH_XXXX"}}

# 查看最近批次
{"skill": "batch.list", "arguments": {"limit": 10}}

# 取消批次
{"skill": "batch.cancel", "arguments": {"batch_id": "BATCH_XXXX"}}

# 查失败任务
{"skill": "task.list_failed", "arguments": {"batch_id": "BATCH_XXXX"}}

# 列出目录文件（仅元数据，不读内容）
{"skill": "file.list_allowed", "arguments": {"path": "E:\\batch_inputs", "max_items": 50}}

# 查看最近日志（自动脱敏 token/key）
{"skill": "log.tail", "arguments": {"log_name": "worker", "lines": 100}}
{"skill": "log.tail", "arguments": {"log_name": "gateway", "lines": 50}}

# ComfyUI 状态
{"skill": "comfyui.status", "arguments": {}}

# Worker 状态
{"skill": "worker.status", "arguments": {}}
```

### 返回格式

成功：
```json
{
  "ok": true,
  "skill": "batch.create",
  "result": {"batch_id": "BATCH_ABCDEF123456", "total_count": 42, "status": "CREATED"},
  "message": "Skill batch.create executed successfully"
}
```

失败：
```json
{"ok": false, "skill": "batch.create", "error": "input_dir does not exist: E:\\xxx"}
```

### 查看调用记录（审计日志）

```bash
GET /skills/v1/calls?limit=20
X-Skill-Token: please_change_me
```

---

## Brain Router 配置

### 工作原理

```
飞书文本消息
    → feishu/event_handler.py
        → brain.router.handle_message(BrainMessage)
            → get_provider() 根据 BRAIN_PROVIDER 选择大脑
                ├── local_command_brain  ← 不需要 API，硬命令
                ├── llm_proxy_brain      ← OpenAI-compatible HTTP
                ├── arkclaw_brain        ← ArkClaw 企业版 HTTP
                ├── claude_brain         ← Anthropic API（stub）
                └── openai_agents_brain  ← OpenAI API（stub）
            → BrainResponse { text, tool_calls }
        → 如果有 tool_calls，自动通过 skills.registry 执行
        → reply_text 回飞书
```

### 消息模式（ARKCLAW_MESSAGE_MODE / BRAIN_MESSAGE_MODE）

```env
# 默认：先检查本地硬命令（help/queue/batch list），不认识再转 AI
ARKCLAW_MESSAGE_MODE=local_command_first

# 所有消息都发给 AI（包括简单命令）
ARKCLAW_MESSAGE_MODE=relay_only
```

### LLM Proxy Brain 工作详情

配置好后，用户在飞书说自然语言，Brain 会：
1. 把用户消息 + 机器状态 + 技能清单 + SOP 摘要一起发给 LLM
2. LLM 输出 JSON：`{"reply": "...", "tool_calls": [{"skill": "batch.create", "arguments": {...}}]}`
3. Brain Router 自动执行 `tool_calls`
4. 汇总结果回复飞书

**修改 System Prompt：** 编辑 `src/assetclaw_matting/brain/llm_proxy_brain.py` 中的 `_SYSTEM_PROMPT`。

**修改 SOP 摘要：** 编辑 `src/assetclaw_matting/brain/context_builder.py` 中的 `build_sop_summary()`。

### 让 AI Brain 了解本节点

把 `skills_pack/assetclaw_win3090/` 里的文档喂给 AI 做知识库：
- `SKILL.md` — 技能参考
- `EXAMPLES.md` — 自然语言到 skill call 的示例
- `SECURITY.md` — 安全限制
- `BATCH_MATTING_SOP.md` — 批量抠图标准流程
- `ANIMATION_AUTOMATION_PLAN.md` — 未来动画自动化规划

### Fallback 链

```
BRAIN_PROVIDER=llm_proxy
  → LLM_PROXY_ENABLED=false 或没有配 API Key
    → BRAIN_FALLBACK_PROVIDER=local_command  ← 永远可用
```

---

## 飞书接入

### 第一步：创建飞书应用

1. [飞书开放平台](https://open.feishu.cn/app) → 创建企业自建应用
2. **凭证与基础信息** → 记录 `App ID` 和 `App Secret` → 填入 `.env`
3. **功能配置** → 机器人 → 启用机器人功能

### 第二步：申请权限

**应用管理 → 权限管理** 申请：
- `im:message`（以机器人身份发消息）
- `im:message.receive_v1`（读取消息）

### 第三步：配置事件订阅

**应用管理 → 事件与回调 → 添加事件**：

1. 订阅 `im.message.receive_v1`
2. **请求 URL**：`https://xxxx.trycloudflare.com/feishu/events`
3. **Verification Token** → 复制到 `.env` 的 `FEISHU_VERIFICATION_TOKEN`

### 第四步：内网穿透

```powershell
cloudflared tunnel --url http://127.0.0.1:7865
# 输出：https://random-name.trycloudflare.com
```

把 `https://random-name.trycloudflare.com/feishu/events` 填入飞书回调 URL，点验证。

> cloudflared 每次重启地址会变，要重填飞书配置。生产环境建议用命名 tunnel 或公司内网网关。

### 第五步：发布应用

飞书开放平台 → 应用管理 → 版本管理 → 创建版本 → 提交审核（企业自建应用一般自审）。

### 飞书命令（本地模式，不需要 AI）

| 发送内容 | 返回 |
|----------|------|
| `help` | 命令列表 |
| `queue` | 当前队列统计 |
| `batch list` | 最近 10 个批次 |
| `batch status BATCH_XXX` | 批次详情（进度、输入输出目录） |
| `batch cancel BATCH_XXX` | 取消批次 |
| `task status <task_id>` | 任务详情和错误信息 |

### 批次通知

创建批次时传 `notify_chat_id`，Gateway 会在批次创建、启动、完成时发通知：

```bash
{
  "skill": "batch.create",
  "arguments": {
    "input_dir": "...", "output_dir": "...",
    "notify_chat_id": "oc_你的群ID"
  }
}
```

群 ID 在飞书事件消息里的 `chat_id` 字段，或通过飞书 API 查询。

---

## 日志和调试

### 日志文件

```
logs/gateway.log   Gateway 所有日志（请求、批次、skill calls、brain 调用）
logs/worker.log    Worker 执行日志（任务状态、ComfyUI 调用、错误）
```

默认：控制台 INFO，文件 DEBUG，每个文件 10MB × 5 个滚动。

### 实时跟踪日志

```powershell
# 实时查看 Worker 日志
Get-Content logs\worker.log -Wait -Tail 50

# 搜索错误
Select-String -Path logs\worker.log -Pattern "ERROR|Exception|FAILED"

# 查看最近 100 条
Get-Content logs\gateway.log -Tail 100
```

### 通过 Skill API 查日志（自动脱敏 token/key/secret）

```bash
curl -X POST http://127.0.0.1:7865/skills/v1/call \
  -H "X-Skill-Token: please_change_me" \
  -H "Content-Type: application/json" \
  -d '{"skill":"log.tail","arguments":{"log_name":"worker","lines":100}}'
```

### 调试数据库

```powershell
# CLI 快速查看
python -m assetclaw_matting.cli.main queue
python -m assetclaw_matting.cli.main batch-list
python -m assetclaw_matting.cli.main task-list --batch-id BATCH_XXX --status FAILED
```

直接查 `data/assetclaw.db`（用 DB Browser for SQLite 或命令行）：

```sql
-- 查看失败任务和错误原因
SELECT id, original_filename, error, finished_at
FROM tasks WHERE status='FAILED' ORDER BY finished_at DESC LIMIT 10;

-- 查看所有 skill 调用记录
SELECT skill, ok, error, requested_by, created_at
FROM skill_calls ORDER BY created_at DESC LIMIT 20;

-- 查看 brain 消息记录（看 AI 在干嘛）
SELECT provider, message_text, response_text, created_at
FROM brain_messages ORDER BY created_at DESC LIMIT 10;

-- 查看批次汇总
SELECT id, status, total_count, succeeded_count, failed_count, created_at
FROM batches ORDER BY created_at DESC LIMIT 10;
```

### ComfyUI 失败调试

当 workflow 运行失败，history JSON 自动保存到：
```
storage/debug/history_{task_id}.json
```

打开后找 `status.messages` 字段，里面有 ComfyUI 的具体报错。

### 调试 Brain LLM

`logs/gateway.log` 里搜索 `llm_proxy`：
- `Brain provider: llm_proxy` → 成功使用 LLM
- `llm_proxy not configured, falling back` → API Key 没填或 enabled=false
- `LLM Proxy call failed` → 网络问题或 API 返回错误

---

## 新增 Skill

详见 `docs/SKILLS_GUIDE.md`。

简要流程：
1. 在 `src/assetclaw_matting/skills/` 里写实现函数
2. 在 `src/assetclaw_matting/skills/registry.py` 的 `SKILL_CATALOG` 里注册
3. 函数签名 = Skill 的参数（直接 `**kwargs` 透传）
4. 返回 `dict`，包含结果数据

---

## Admin API 速查

无需 Token：

```bash
GET  /health                               # 服务信息
GET  /admin/queue                          # 队列统计
GET  /admin/batches?status=RUNNING         # 批次列表
GET  /admin/batches/{id}                   # 批次详情
POST /admin/batches/create                 # 创建批次（JSON body）
POST /admin/batches/{id}/start             # 启动批次
POST /admin/batches/{id}/cancel            # 取消批次
GET  /admin/tasks?batch_id=X&status=FAILED # 任务列表
GET  /admin/tasks/{id}                     # 任务详情
GET  /admin/worker/status                  # Worker 状态
GET  /admin/comfyui/status                 # ComfyUI 状态
GET  /arkclaw/status                       # ArkClaw 连接状态
GET  /arkclaw/context                      # 当前机器上下文（调试用）
GET  /mcp/tools                            # MCP 工具列表（给 Claude/Cursor 用）
```

Worker API（需要 `X-Worker-Token`）：

```bash
GET  /worker/tasks/next                    # 拉取下一任务
POST /worker/tasks/{id}/started            # 标记开始
GET  /worker/tasks/{id}/input              # 下载输入文件（远程 Worker 用）
POST /worker/tasks/{id}/succeeded          # 上报成功 + 输出路径
POST /worker/tasks/{id}/failed             # 上报失败 + 错误信息
```

---

## CLI 命令速查

```powershell
# 基础管理
python -m assetclaw_matting.cli.main init-db              # 初始化数据库
python -m assetclaw_matting.cli.main gateway              # 启动 Gateway
python -m assetclaw_matting.cli.main worker               # 启动 Worker

# 批次管理
python -m assetclaw_matting.cli.main batch-create `
    --input-dir  "E:\input" `
    --output-dir "E:\output" `
    --workflow-type matting_v1 `
    --notify-chat-id "oc_xxx"          # 可选

python -m assetclaw_matting.cli.main batch-start  --batch-id BATCH_XXX
python -m assetclaw_matting.cli.main batch-list   [--limit 20]
python -m assetclaw_matting.cli.main batch-status --batch-id BATCH_XXX

# 任务查看
python -m assetclaw_matting.cli.main task-list    [--batch-id X] [--status FAILED]
python -m assetclaw_matting.cli.main queue
```

---

## 常见问题

**Skill API 返回 401**
→ 检查 Header `X-Skill-Token` 和 `.env` 里 `SKILL_API_TOKEN` 是否一致。

**Worker 启动后不拉任务**
→ 批次需要先 start（`batch-start`）。只有 `status=RUNNING` 的批次里的任务才会被拉取。

**`No LoadImage node found`**
→ workflow JSON 必须用 ComfyUI **API Format** 导出，不是普通 Save。确认文件里有 `"class_type": "LoadImage"`。

**`No SaveImage outputs found`**
→ workflow 跑完没有输出节点。查 `storage/debug/history_{task_id}.json`，看 `status.messages`。

**worker.lock 导致 Worker 无法启动**
→ 删除项目根目录下的 `worker.lock` 文件。

**Brain 没有调用 LLM，走了本地命令**
→ 检查 `LLM_PROXY_ENABLED=true` 且 URL 和 API Key 已填写。看 `logs/gateway.log` 里的 `falling back` 警告。

**飞书消息收不到**
→ (1) cloudflared 是否在跑，地址是否最新。(2) 应用是否已发布。(3) 订阅了 `im.message.receive_v1` 事件。

**CUDA OOM（显存不足）**
→ 降低输入图片分辨率，或重启 ComfyUI 清空显存。`AGENT_RUNS_ON_GPU` 永远保持 `false`。

**路径被拒绝（Path not in allowed roots）**
→ 在 `.env` 的 `ALLOWED_ROOTS` 加上所需路径前缀（分号分隔）。

**重置数据库**
→ 删除 `data/assetclaw.db`，重新 `python -m assetclaw_matting.cli.main init-db`。
