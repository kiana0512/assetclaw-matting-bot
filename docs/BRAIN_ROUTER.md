# Brain Router

当前主线是 LLM Proxy + 自建 Brain Router，因为 Win3090 只负责执行，不本地跑 LLM。LLM Proxy 输出严格 JSON tool calls，本地执行 skill 后，再把结构化结果交给模型总结成飞书可读文本。

公司 Lilith API 当前按 Claude Code 的 Anthropic-compatible 方式接入：`LLM_PROXY_BASE_URL=https://llm-proxy.lilithgames.com`，代码会请求 `/v1/messages`。Atlas 助手会把原始 LLM Proxy Key 转换成 Claude Code 使用的 `ANTHROPIC_AUTH_TOKEN`；本项目的 `.env` 应把这个 token 写入 `LLM_PROXY_API_KEY`，并设置 `LLM_PROXY_AUTH_HEADER=authorization_bearer`。

本地记忆保存在 SQLite：`brain_messages` 存近期对话和 tool calls，`conversation_summaries` 存自动压缩后的旧对话摘要，`memory_notes` 存长期结构化记忆。飞书消息会使用 `chat_id + open_id` 生成 `conversation_id`，因此同一个群里的不同用户也会各走各的上下文和记忆。Brain 每次调用 LLM 前只读取同一 `conversation_id` 的摘要、近期消息和长期记忆作为上下文。显式“请记住”类请求会通过 `memory.remember` 写入当前用户会话 scope。

## 自动压缩

为了避免上下文无限增长，写入 `brain_messages` 后会自动检查当前会话消息量：

- 超过 `BRAIN_MEMORY_COMPACT_AFTER_MESSAGES` 后触发压缩。
- 默认保留最近 `BRAIN_MEMORY_COMPACT_KEEP_MESSAGES` 条完整原文。
- 更早的消息会合并进 `conversation_summaries.summary_text`。
- 被压缩过的旧 `brain_messages` 原文会从表里删除。
- 压缩不调用 LLM，使用本地确定性摘要，失败不会影响正常回复。
- 如果当前消息来自飞书，压缩成功时会向当前会话发一条简短提示。

操作员可看 `logs\conversation.log` 观察完整链路：飞书入站文本、Brain 输入、模型选择的 tool calls、skill 执行结果、Brain 最终回复、飞书出站回复。日志不会展示模型隐藏思维链，只展示可审计的决策和工具调用。

本地检查 LLM Proxy：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\test_llm_proxy.ps1
```

如果返回 401 且提示 `Invalid proxy server token`，说明请求已经到达公司代理，但 `.env` 中的 `LLM_PROXY_API_KEY` 未被代理接受。此时优先重新从 Atlas 助手复制当前正在使用的 Lilith LLM API-Key，而不是调整模型名。

Brain 不能直接 shell、不能删除、不能读 secret，只能调 registry 暴露的 skills。

