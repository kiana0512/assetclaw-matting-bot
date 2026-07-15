# Brain Router

当前主线是 DeepSeek API + 自建 Brain Router，因为 Win3090 只负责执行，不本地跑 LLM。DeepSeek 输出严格 JSON tool calls，本地执行 skill 后，再把结构化结果交给模型总结成飞书可读文本。

DeepSeek 按 OpenAI-compatible Chat Completions 接入：`DEEPSEEK_BASE_URL=https://api.deepseek.com`，代码请求 `/chat/completions`。生产推荐 `.env` 设置 `BRAIN_PROVIDER=deepseek`、`DEEPSEEK_API_KEY`、`DEEPSEEK_ROUTER_MODEL=deepseek-v4-flash`、`DEEPSEEK_SUMMARY_MODEL=deepseek-v4-pro`。

旧 `LLM_PROXY_*` 配置仅保留兼容，不再是生产主线。

## 当前入口顺序

`LocalCommandBrain` 和 `LLMProxyBrain` 现在共用 `brain.pre_llm_router.handle_pre_llm_message` 做前置确定性路由，避免两个入口各写一套判断导致行为漂移。顺序如下：

1. 语音回复模式：识别“开启语音回复/关闭语音回复”，写入会话记忆。
2. 语音附件：优先走本地 ASR，识别文本后重新进入同一套 Brain 路由；不会落到图片 OCR。
3. 最近图片问答：处理“刚刚那张图在哪里/收到了吗”等引用型问题。
4. 翻译和 OCR：文本翻译、图片文字提取、图片文字翻译。
5. 多模态附件：保存、预览、复制、图片分析等附件任务。
6. 对话召回：回答“刚才说了什么/刚刚那个文件”等近期上下文问题。
7. 生活/工具确定性 planner：GPU、当前任务、文件操作等无需 LLM 推理的能力。
8. Provider 后续逻辑：Local 继续 `_infer_tool_calls`；LLM Proxy 再调用模型做复杂工具决策。

这个顺序的目标是：语音优先于图片/OCR，明确工具意图优先于闲聊，只有前面都不命中时才进入 LLM 或情绪回复。

## 唱歌和接歌词

唱歌模式、接歌词、续下一句功能已经关闭。Brain Router 不再读取或写入唱歌模式会话状态；即使旧数据库里残留 `conversation_mode=sing_along`，也不会影响新消息路由。用户说“进入唱歌模式”“不要再接下一句”等，只会得到简短关闭说明，然后继续按正常聊天或工具指令处理。

## ASR/TTS 链路

语音输入由 `brain.speech_planner` 接管，具体 ASR/TTS 实现在 `skills.speech_skills`：

- ASR 默认使用 FunASR / ModelScope `iic/SenseVoiceSmall`，本地模型目录是 `storage\models\asr\iic__SenseVoiceSmall`。
- ASR fallback 支持 `faster-whisper` / `openai-whisper`。
- TTS 默认使用 IndexTTS2，模型目录是 `storage\models\index-tts\checkpoints`，repo 目录是 `storage\models\index-tts\repo`。
- TTS fallback 支持 `edge-tts` 和 `pyttsx3`。
- 飞书语音消息会先回复耗时提示：ASR 通常 2-8 秒，首次加载可能 20 秒以上；如果还要 TTS，会先发文字，再补发语音。

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

本地检查 DeepSeek：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\test_deepseek_api.ps1
```

如果返回 401，检查 `DEEPSEEK_API_KEY`；如果返回 402，检查 DeepSeek 余额/计费；如果返回 404，检查 `DEEPSEEK_BASE_URL` 和模型名是否为 `deepseek-v4-flash` / `deepseek-v4-pro`。

Brain 不能直接 shell、不能删除、不能读 secret，只能调 registry 暴露的 skills。

