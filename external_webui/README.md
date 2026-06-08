# Miku Agent External WebUI

本目录是独立的 Vue 3 本机 WebUI，不做内网穿透，不绑定公网地址。它可以脱离 Agent 后端单独启动；没连上后端时界面仍可打开，但聊天、状态和技能能力会显示为离线。

## 启动

开发模式：

```powershell
cd .\external_webui
npm run dev
```

构建并用本机 Python 服务运行：

```powershell
npm run build
cd ..
python .\external_webui\server.py
```

然后打开：

```text
http://127.0.0.1:5177
```

默认代理到本机 Agent 后端，会优先读取项目 `.env` 里的 `GATEWAY_BASE_URL` / `GATEWAY_PORT`。当前项目默认是：

```text
http://127.0.0.1:7865
```

如后端端口不同：

```powershell
$env:ASSETCLAW_AGENT_URL="http://127.0.0.1:8010"
python .\external_webui\server.py
```

## 技能 Token

后端 `/skills/v1/call` 需要 `X-Skill-Token`。WebUI 的 Vite 代理和 Python 本机代理会自动读取项目根目录 `.env` 里的 `SKILL_API_TOKEN` 并注入请求头，通常不需要在前端手填。

页面里的“连接设置”只用于手动覆盖 token。这个值只保存在浏览器 `localStorage`。

## 安全边界

- WebUI 服务器默认只监听 `127.0.0.1`。
- 代理目标默认只允许 `localhost`、`127.0.0.1`、`::1`。
- 不包含 Cloudflare Tunnel、ngrok、frp、反向 SSH 或任何公网暴露逻辑。
- 任务启动、暂停、继续、终止都通过后端 skill API，不绕过后端确认和安全规则。
- 路径浏览器通过 `file.list_allowed` 列出允许目录，不读取 `.env`、系统目录等被后端拒绝的路径。

## 页面

- 对话：对接 `/brain/test`，保留自然语言入口。
- 总控：模块状态、诊断、参数入口。
- 队列：ComfyUI / Cherry / 抽帧 / Pipeline / 自定义流程任务和控制。
- 流程：动画自动化流程时间线与工作区计数。
- 编排：自定义工作流步骤、变量、参数 JSON。
- 语音：ASR / TTS。
- P4：workspace、opened、reconcile、depot 对比。
- 记忆：Memory / RAG context pack 和压缩。
- 技能：后端 skills manifest。
- 日志：Agent 对话日志和 skill 调用日志。

## 视觉主题

当前主题是 Miku Agent：青绿/薄荷绿应援色、虚拟歌姬智能体背景、透明玻璃控制台。背景资产在：

```text
external_webui/assets/miku-agent-bg-4k.png
```
