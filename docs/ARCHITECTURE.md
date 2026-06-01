# Architecture

数据流：

```text
飞书 -> Gateway -> Brain Router -> LLM Proxy -> Skill Registry -> Win3090
    -> Skill Result -> LLM Summary -> 飞书
```

Feishu 只做消息入口和出口。Brain Router 选择 `llm_proxy`，不可用时 fallback 到 `local_command`。所有机器动作必须通过 Skill Registry；Registry 负责统一调度、异常捕获和 SQLite audit。

未来兼容 provider 放在 `brain/providers_reserved.py`：ArkClaw、Claude SDK、OpenAI Agents、LangGraph。它们不是当前主线。
