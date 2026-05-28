# AssetClaw Matting Bot

> ComfyUI 批量抠图 + 飞书通知 + Agent Harness 预留

## 项目定位

**AssetClaw Matting Bot** 是一个面向公共 Windows 3090 机器的 ComfyUI 批处理调度系统。

核心角色：
- **Gateway / Control Plane** — 接受批次创建、管理任务队列、飞书消息接入、Admin API
- **Worker / Execution Plane** — 单 Worker 从 Gateway 拉任务、调用本地 ComfyUI、回报结果
- **Feishu Channel** — 消息入口 + 批次通知 + 状态查询
- **Agent Harness**（预留）— 通过外部 API 接入大模型，不占用本地 3090 显存

当前第一阶段 MVP 目标：

```
指定输入目录 → 批量抠图 → 结果保存到输出目录
```

---

## 架构图

```
[飞书 / CLI / Admin API]
         │
         ▼
  ┌─────────────────────┐
  │  Gateway (FastAPI)  │  ← 任何机器，含内网穿透
  │  SQLite 任务队列     │
  │  飞书消息接入        │
  │  Admin REST API     │
  └──────────┬──────────┘
             │ HTTP 轮询 (X-Worker-Token 鉴权)
             ▼
  ┌─────────────────────┐
  │  Worker (本地)       │  ← Windows 3090 机器
  │  单 Worker 顺序处理  │
  │  调用本地 ComfyUI   │
  └──────────┬──────────┘
             │
             ▼
  ┌─────────────────────┐
  │  ComfyUI API        │  http://127.0.0.1:8188
  │  (本地 GPU 执行)    │
  └─────────────────────┘

  ┌─────────────────────┐
  │  Agent Harness      │  ← 预留，外部 API，不占 GPU
  │  (AGENT_ENABLED=f)  │
  └─────────────────────┘
```

---

## 快速开始

### 1. 环境安装

```powershell
conda create -n assetclaw-matting python=3.11 -y
conda activate assetclaw-matting
pip install -r requirements.txt
```

或一键初始化：

```powershell
.\scripts\init_project.ps1
```

### 2. 配置 .env

```powershell
Copy-Item .env.example .env
# 编辑 .env
```

关键配置项：

| 变量 | 说明 | 默认 |
|------|------|------|
| `FEISHU_APP_ID` | 飞书应用 ID | — |
| `FEISHU_APP_SECRET` | 飞书应用 Secret | — |
| `FEISHU_VERIFICATION_TOKEN` | 飞书事件验证 token | — |
| `WORKER_TOKEN` | Gateway ↔ Worker 鉴权密钥 | `please_change_me` |
| `COMFYUI_URL` | ComfyUI 地址 | `http://127.0.0.1:8188` |
| `COMFYUI_WORKFLOW_PATH` | workflow API JSON 路径 | `workflows/matting_api.json` |
| `COMFYUI_FAKE_MODE` | 跳过 ComfyUI，用 Pillow mock | **`true`** |
| `ALLOWED_ROOTS` | 允许的目录前缀（分号分隔） | 见 .env.example |

> **COMFYUI_FAKE_MODE 默认 true**。无 GPU 时可直接测试全链路。

### 3. 初始化数据库

```powershell
python -m assetclaw_matting.cli.main init-db
```

---

## Fake Mode 全链路测试（无 GPU）

确认 `.env` 中 `COMFYUI_FAKE_MODE=true`，然后按以下顺序执行：

**终端 1 — 启动 Gateway：**
```powershell
conda activate assetclaw-matting
python -m assetclaw_matting.cli.main gateway
```

**终端 2 — 创建并启动批次：**
```powershell
# 先往 batch_inputs 放几张图片
python -m assetclaw_matting.cli.main batch-create `
    --input-dir E:\assetclaw-matting-bot\storage\batch_inputs `
    --output-dir E:\assetclaw-matting-bot\storage\batch_outputs `
    --workflow-type matting_v1

python -m assetclaw_matting.cli.main batch-list
python -m assetclaw_matting.cli.main batch-start --batch-id BATCH_XXXXXXXXXXXX
```

**终端 3 — 启动 Worker：**
```powershell
python -m assetclaw_matting.cli.main worker
```

**预期结果：**
- `storage/batch_outputs/` 出现 `*_matting.png` 文件
- `python -m assetclaw_matting.cli.main batch-status --batch-id BATCH_XXX` 显示 `SUCCEEDED`
- `logs/worker.log` 有处理记录

---

## 真实 ComfyUI 测试

### 启动 ComfyUI

```powershell
cd E:\ComfyUI
python main.py --listen 127.0.0.1 --port 8188
```

### 导出 Workflow

1. ComfyUI 界面：Settings → Enable Dev Mode Options → 打开
2. 搭好抠图工作流，确保含 **LoadImage** 和 **SaveImage** 节点
3. 点击 **Save (API Format)** → 保存为 `workflows/matting_api.json`

参考格式：`workflows/matting_api.example.json`

### 修改 .env

```
COMFYUI_FAKE_MODE=false
COMFYUI_WORKFLOW_PATH=E:\assetclaw-matting-bot\workflows\matting_api.json
```

然后按照 Fake Mode 流程启动 Gateway → 创建批次 → 启动 Worker。

---

## 飞书接入

### 启动 cloudflared 内网穿透

```powershell
cloudflared tunnel --url http://127.0.0.1:7865
# 记录输出的 https://xxxx.trycloudflare.com 地址
```

> cloudflared 每次重启地址会变，需更新飞书回调 URL。

### 飞书回调配置

1. 开放平台 → 应用 → 事件与回调
2. 回调 URL：`https://xxxx.trycloudflare.com/feishu/events`
3. 订阅事件：`im.message.receive_v1`

所需权限：
- 读取单聊 / 群聊消息
- 以机器人身份发送消息

### 飞书命令

| 命令 | 说明 |
|------|------|
| `help` | 查看帮助 |
| `queue` | 查看当前队列 |
| `batch list` | 最近 10 个批次 |
| `batch status <batch_id>` | 批次详情 |
| `batch cancel <batch_id>` | 取消批次 |
| `task status <task_id>` | 任务详情 |

---

## CLI 命令速查

```powershell
python -m assetclaw_matting.cli.main init-db
python -m assetclaw_matting.cli.main gateway
python -m assetclaw_matting.cli.main worker
python -m assetclaw_matting.cli.main batch-create --input-dir X --output-dir Y
python -m assetclaw_matting.cli.main batch-start  --batch-id BATCH_XXX
python -m assetclaw_matting.cli.main batch-list   [--limit 20]
python -m assetclaw_matting.cli.main batch-status --batch-id BATCH_XXX
python -m assetclaw_matting.cli.main task-list    [--batch-id X] [--status FAILED]
python -m assetclaw_matting.cli.main queue
```

---

## Admin API 速查

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/admin/queue` | 队列状态 |
| GET | `/admin/batches` | 批次列表 |
| GET | `/admin/batches/{id}` | 批次详情 |
| POST | `/admin/batches/create` | 创建批次 |
| POST | `/admin/batches/{id}/start` | 启动批次 |
| POST | `/admin/batches/{id}/cancel` | 取消批次 |
| GET | `/admin/tasks` | 任务列表 |
| GET | `/admin/tasks/{id}` | 任务详情 |
| GET | `/admin/worker/status` | Worker 状态 |
| GET | `/admin/comfyui/status` | ComfyUI 状态 |

---

## Worker API（内部）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/worker/tasks/next` | 拉取下一个任务 |
| POST | `/worker/tasks/{id}/started` | 标记开始 |
| GET | `/worker/tasks/{id}/input` | 下载输入文件（远程 Worker） |
| POST | `/worker/tasks/{id}/succeeded` | 上报成功 + 输出路径 |
| POST | `/worker/tasks/{id}/failed` | 上报失败 |

所有 Worker 接口需要 Header：`X-Worker-Token: <WORKER_TOKEN>`

---

## Agent Harness（预留）

```
AGENT_ENABLED=false   # 默认：走确定性命令解析
AGENT_ENABLED=true    # 接外部 LLM API（不占 3090 显存）
```

当 `AGENT_ENABLED=true` 时：
- 通过 `AGENT_LLM_BASE_URL` 调用 OpenAI-compatible API
- LLM 通过白名单工具控制系统
- **不执行 shell，不访问任意路径，不删除文件**

已注册工具：
`batch_create / batch_start / batch_status / batch_list / batch_cancel /
queue_status / worker_status / comfyui_status / task_list_failed`

---

## 目录结构

```
assetclaw-matting-bot/
├── src/assetclaw_matting/
│   ├── config.py               所有配置项（pydantic-settings）
│   ├── logging_setup.py
│   ├── db/                     SQLite 层（sqlite / schema / task_repo / batch_repo）
│   ├── models/                 Pydantic 数据模型
│   ├── services/               业务逻辑（batch / task / file_store / notification）
│   ├── feishu/                 飞书 client + 事件处理 + 命令解析
│   ├── comfyui/                ComfyUI client + workflow patch
│   ├── api/                    FastAPI 路由（feishu / worker / admin）
│   ├── worker/                 Worker 轮询 + lock
│   ├── agent/                  Agent Harness（harness / llm_client / tools / ...）
│   └── cli/                    CLI 入口
├── storage/
│   ├── batch_inputs/           默认输入目录
│   ├── batch_outputs/          默认输出目录
│   ├── tasks/                  每任务元数据（task.json, 调试文件）
│   ├── batches/                批次级文件（预留）
│   └── debug/                  ComfyUI history 调试文件
├── workflows/                  ComfyUI API JSON 文件
├── data/                       SQLite 数据库
├── logs/                       gateway.log / worker.log
└── scripts/                    PowerShell 启动脚本
```

---

## 常见问题

| 问题 | 解决方案 |
|------|----------|
| cloudflared 地址变了 | 重新填写飞书回调 URL |
| 飞书收不到消息 | 检查应用是否发布、权限是否开通 |
| `invalid token` | `FEISHU_VERIFICATION_TOKEN` 与飞书控制台不一致 |
| ComfyUI 连不上 | 确认 ComfyUI 在 `COMFYUI_URL` 地址运行 |
| `No LoadImage node` | 检查 workflow JSON，确保用 API Format 导出 |
| `No SaveImage outputs` | 查看 `storage/debug/history_*.json` 调试 |
| worker.lock 导致无法启动 | 删除项目根目录下的 `worker.lock` |
| 显存不足 | 降低输入分辨率，重启 ComfyUI |
| 路径不在 allowed_roots | 在 `.env` 的 `ALLOWED_ROOTS` 里添加路径 |

---

## 后续扩展路线

- **新增 workflow_type**：在 `configs/workflows.example.yaml` 添加，Worker 按 `workflow_type` 分发
- **新增 ComfyUI workflow**：导出 JSON → `workflows/` → 更新 `.env`
- **接入 Agent**：设置 `AGENT_ENABLED=true` + `AGENT_LLM_BASE_URL`，接 Claude / GPT-4 等
- **多 Worker**：多台机器独立 `WORKER_ID`，队列天然支持多 Worker 并发
- **Web 管理后台**：基于 Admin API 扩展前端
