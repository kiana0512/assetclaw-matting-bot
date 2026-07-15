# Architecture

数据流：

```text
飞书 -> Gateway -> Brain Router -> LLM Proxy -> Skill Registry -> Win3090
    -> Skill Result -> LLM Summary -> 飞书
```

Feishu 只做消息入口和出口。Brain Router 选择 `llm_proxy`，不可用时 fallback 到 `local_command`。所有机器动作必须通过 Skill Registry；Registry 负责统一调度、异常捕获和 SQLite audit。

## Python 环境边界

本项目固定分成两个 Python 环境，不能混用：

- `assetclaw` conda 环境：运行 Agent、Gateway、飞书长连接、Brain Router、Skills、ASR/TTS、P4 助手、测试脚本和文档/表格/文件类自动化。
- 秋叶 ComfyUI 环境：只用于启动 ComfyUI 后端，以及 Cherry 帧序列处理 worker 这类依赖秋叶 PyTorch/ComfyUI 运行时的图像管线。

ComfyUI 批量抠图 skill 本身运行在 `assetclaw` 环境里，但它只通过 HTTP 调用秋叶环境启动的 ComfyUI 服务：`http://127.0.0.1:8188`。也就是说，Agent 不把 ComfyUI 依赖装进 `assetclaw`，也不修改秋叶环境。

Cherry worker 会使用：

```text
<comfyui-root>\python\python.exe
```

这是为了复用秋叶环境里的 torch/opencv/Pillow 等图像处理依赖。其他 Python 代码一律使用 `assetclaw`。

未来兼容 provider 放在 `brain/providers_reserved.py`：ArkClaw、Claude SDK、OpenAI Agents、LangGraph。它们不是当前主线。
 