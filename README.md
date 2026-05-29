# AssetClaw Win3090 Skill Node

> ArkClaw 企业版中央大脑的本地执行节点
>
> Feishu Channel + ArkClaw Bridge + Skill Gateway + ComfyUI Batch Worker

## 项目定位

**本项目不是普通飞书机器人。**

本项目是 **ArkClaw 企业版**控制 Windows 3090 机器的**本地 Skill Node**。

| 组件 | 角色 | 在哪跑 |
|------|------|-------|
| 飞书机器人 | 消息入口和通知渠道 | 飞书云 |
| ArkClaw Enterprise Brain | 超级大脑：自然语言理解、任务规划、记忆 | 公司云 |
| Skill Gateway | 受控技能接口，ArkClaw 的"遥控器" | 本机 |
| Batch / Task Control Plane | 任务队列、SQLite、Admin API | 本机 |
| Worker | 单 Worker 顺序执行 GPU 任务 | 本机（3090） |
| ComfyUI | 图像处理管线 | 本机（3090 GPU） |

**关键原则：**
- 3090 显存只留给 ComfyUI 图像任务，**不跑本地大模型**
- ArkClaw 在云端做 AI 推理，通过 Skill API 控制本机
- 飞书只是消息管道，不做业务决策

---

## 架构一览

```
用户
 ↓ (飞书自然语言)
ArkClaw Enterprise Brain  [云端]
 ↓ POST /skills/v1/call  X-Skill-Token
Skill Gateway  [本机 :7865]
 ↓
Batch / Task Control Plane  [SQLite]
 ↓ HTTP 轮询
Single Worker  [本机]
 ↓
Local ComfyUI  http://127.0.0.1:8188  [3090 GPU]
 ↓
输出目录 / 飞书通知
```

---

## 快速开始

### 1. 安装

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
# 编辑 .env，填入真实值
```

关键配置项：

| 变量 | 说明 | 默认 |
|------|------|------|
| `SKILL_API_TOKEN` | Skill Gateway 鉴权 token | `please_change_me` |
| `WORKER_TOKEN` | Worker 鉴权 token | `please_change_me` |
| `COMFYUI_FAKE_MODE` | true=Pillow mock（测试用） | **`true`** |
| `ARKCLAW_ENABLED` | 启用 ArkClaw 云端 AI | `false` |
| `ALLOWED_ROOTS` | 允许的路径前缀（分号分隔） | 见 .env.example |

### 3. 初始化数据库

```powershell
python -m assetclaw_matting.cli.main init-db
```

---

## Fake Mode 批量抠图测试（无 GPU）

确认 `.env` 中 `COMFYUI_FAKE_MODE=true`：

```powershell
# 终端 1 — Gateway
python -m assetclaw_matting.cli.main gateway

# 终端 2 — 创建并启动批次（先往 batch_inputs 放几张图）
python -m assetclaw_matting.cli.main batch-create `
    --input-dir  E:\assetclaw-matting-bot\storage\batch_inputs `
    --output-dir E:\assetclaw-matting-bot\storage\batch_outputs
python -m assetclaw_matting.cli.main batch-start --batch-id BATCH_XXX

# 终端 3 — Worker
python -m assetclaw_matting.cli.main worker
```

**预期：**
- `storage/batch_outputs/*.png` 出现结果文件
- `batch-status` 显示 `SUCCEEDED`

---

## Skill Gateway 测试

```powershell
# 获取技能清单
curl -H "X-Skill-Token: please_change_me" http://127.0.0.1:7865/skills/v1/manifest

# 调用 queue.status
curl -X POST http://127.0.0.1:7865/skills/v1/call `
  -H "X-Skill-Token: please_change_me" `
  -H "Content-Type: application/json" `
  -d '{"skill":"queue.status","arguments":{}}'

# 通过 Skill API 创建批次
curl -X POST http://127.0.0.1:7865/skills/v1/call `
  -H "X-Skill-Token: please_change_me" `
  -H "Content-Type: application/json" `
  -d '{"skill":"batch.create","arguments":{"input_dir":"E:\\storage\\batch_inputs","output_dir":"E:\\storage\\batch_outputs"}}'
```

---

## 当前可用 Skills（12 个已实现）

| Skill | 用途 | 风险 |
|-------|------|------|
| `batch.create` | 创建批量抠图任务 | 中 |
| `batch.start` | 启动已创建批次 | 低 |
| `batch.status` | 查看批次进度 | 低 |
| `batch.list` | 列出最近批次 | 低 |
| `batch.cancel` | 取消排队任务 | 中 |
| `queue.status` | 全局队列统计 | 低 |
| `task.status` | 任务详情 | 低 |
| `task.list_failed` | 失败任务列表 | 低 |
| `worker.status` | Worker 活动 | 低 |
| `comfyui.status` | ComfyUI 在线状态 | 低 |
| `file.list_allowed` | 列出允许路径文件（仅元数据） | 低 |
| `log.tail` | 查看最近日志（脱敏） | 低 |

预留 4 个（未实现）：`frame.extract` `model3d.generate` `texture.apply` `workflow.run`

---

## 飞书命令（本地硬命令，无需 ArkClaw）

| 命令 | 说明 |
|------|------|
| `help` | 查看帮助 |
| `queue` | 队列状态 |
| `batch list` | 最近批次 |
| `batch status <id>` | 批次详情 |
| `batch cancel <id>` | 取消批次 |
| `task status <id>` | 任务详情 |

---

## ArkClaw 接入

```env
# .env
ARKCLAW_ENABLED=true
ARKCLAW_BASE_URL=https://your.arkclaw.api
ARKCLAW_API_KEY=your_key
ARKCLAW_MESSAGE_MODE=local_command_first
```

配合 cloudflared 暴露 Skill API：
```powershell
cloudflared tunnel --url http://127.0.0.1:7865
# ArkClaw Skill Node URL: https://xxxx.trycloudflare.com
```

详细接入文档：[docs/ARKCLAW_INTEGRATION.md](docs/ARKCLAW_INTEGRATION.md)

---

## 真实 ComfyUI 接入

1. 启动 ComfyUI：
   ```powershell
   cd E:\ComfyUI && python main.py --listen 127.0.0.1 --port 8188
   ```
2. 导出 workflow：Settings → Enable Dev Mode → Save (API Format) → `workflows/matting_api.json`
3. 修改 `.env`：`COMFYUI_FAKE_MODE=false`
4. 确保 workflow 有 `LoadImage` 和 `SaveImage` 节点

详细说明：[docs/COMFYUI_WORKFLOW.md](docs/COMFYUI_WORKFLOW.md)

---

## CLI 速查

```powershell
python -m assetclaw_matting.cli.main init-db
python -m assetclaw_matting.cli.main gateway
python -m assetclaw_matting.cli.main worker
python -m assetclaw_matting.cli.main batch-create --input-dir X --output-dir Y
python -m assetclaw_matting.cli.main batch-start  --batch-id BATCH_XXX
python -m assetclaw_matting.cli.main batch-list
python -m assetclaw_matting.cli.main batch-status --batch-id BATCH_XXX
python -m assetclaw_matting.cli.main task-list    [--batch-id X] [--status FAILED]
python -m assetclaw_matting.cli.main queue
```

---

## 文档目录

| 文档 | 内容 |
|------|------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 总体架构和数据流 |
| [docs/ARKCLAW_INTEGRATION.md](docs/ARKCLAW_INTEGRATION.md) | ArkClaw 接入完整指南 |
| [docs/SKILLS.md](docs/SKILLS.md) | Skill 参考手册 |
| [docs/SECURITY.md](docs/SECURITY.md) | 安全模型 |
| [docs/BATCH_MATTING_SOP.md](docs/BATCH_MATTING_SOP.md) | 批量抠图 SOP（供 ArkClaw 学习） |
| [docs/FEISHU_SETUP.md](docs/FEISHU_SETUP.md) | 飞书配置指南 |
| [docs/COMFYUI_WORKFLOW.md](docs/COMFYUI_WORKFLOW.md) | ComfyUI workflow 接入 |
| [docs/FUTURE_SKILLS.md](docs/FUTURE_SKILLS.md) | 后续技能路线图 |
| [docs/OPENCLAW_INTEGRATION.md](docs/OPENCLAW_INTEGRATION.md) | ~~OpenClaw~~ 历史兼容说明 |

---

## 常见问题

| 问题 | 解决 |
|------|------|
| Skill API 返回 401 | 检查 `SKILL_API_TOKEN` |
| Worker 不拉任务 | 确认 batch 已 start（`batch-status`） |
| ComfyUI 超时 | 检查 VRAM 使用；重启 ComfyUI |
| `No LoadImage node` | 确认用 API Format 导出 workflow |
| worker.lock 卡住 | 删除项目根目录的 `worker.lock` |
| 路径拒绝 | 把路径加入 `ALLOWED_ROOTS` |
| ArkClaw 无响应 | 检查 `ARKCLAW_BASE_URL` 和 `ARKCLAW_API_KEY` |
