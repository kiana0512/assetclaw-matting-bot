# LilClick Animation WebUI

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
http://127.0.0.1:5180
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

- 总览：当前动画流程、真实子任务进度、七步生产链路、处理配置与工作区产物。
- 任务：动画流程、ComfyUI、抽帧和 Cherry 任务列表与安全控制。
- 启动：按日期启动完整动画流程；FPS 可填写任意正整数，默认 `24`；P4 默认关闭。
- Agent：对接 `/brain/test`，保留自然语言和附件入口。

启动页只处理飞书指定视图内「进度」精确等于 `待抽帧` 且带动画附件的记录。FPS 会原样传给 `animation_flow.start`，不会修改视频原文件。总览中的抽帧、抠图和后处理进度绑定当前 AFLOW 自己保存的子任务 ID，不使用全局最新任务冒充当前流程。

## 视觉主题

当前主题使用 LilClick 视觉系统：紫黑工作画布、中性灰卡片、紫到粉的操作强调色，同时保留浅色模式和移动端响应式布局。
