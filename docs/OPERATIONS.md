# Operations

## Git 与项目清理

运行数据不得提交到 Git。提交前执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\check_repo_hygiene.ps1
git diff --check
git status --short
```

项目清理默认是 dry-run。确认预览后再执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\clean_project.ps1 -Apply
```

该命令保留 `.env`、数据库及其备份、任务输入输出和业务资产。完整追踪规则与误提交恢复方法见 [Git 仓库与运行数据管理](REPOSITORY_HYGIENE.md)。

## 一键重启本地机器人（推荐，公司内网标准方式）

```powershell
cd <project-root>
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start_bot_local.ps1
```

`start_bot_local.ps1` 是后端维护的唯一推荐入口。它会：

1. 停掉旧的 Gateway（端口 `7865`）
2. 停掉旧的 WebUI（端口 `5180`）
3. 停掉旧的飞书 WS Receiver
4. 初始化数据库
5. 后台隐藏启动本地 Gateway（`http://127.0.0.1:7865`）
6. 后台隐藏启动飞书长连接 WS Receiver
7. 后台隐藏启动 WebUI（`http://127.0.0.1:5180`）
8. 当前窗口显示系统状态，并持续 tail `logs\conversation.log`

日常不要手动 kill 端口再逐个启动服务，除非在排查 `start_bot_local.ps1` 本身。

关闭这个日志窗口不会自动停止后台服务。停止服务请运行 `scripts\stop_bot_local.ps1`。

## 停止

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\stop_bot_local.ps1
```

## 单独启动

```powershell
# 仅 Gateway（本地调试）
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start_local_gateway.ps1

# 仅飞书 WS 接收器
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start_feishu_ws.ps1

# 仅 WebUI
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\start_external_webui.ps1
```

单独启动只用于定位问题。正常恢复服务用 `start_bot_local.ps1`。

## 清理缓存和运行产物

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\clean_project.ps1
```

清理范围：

- Python 缓存：`__pycache__`、`.pytest_cache`、`.mypy_cache`、`.ruff_cache`
- 临时运行记录：`storage/agent_jobs`、`storage/animation_flow_runner`、`storage/animation_flow_runs`、`storage/custom_pipeline_runs`
- WebUI 临时上传：`storage/webui_uploads`
- 已确认重复的根目录临时文件：`SpriteAtlasGeneratorTool.cs`

保留范围：

- `.env`
- `data/assetclaw.db`
- 非 Cloudflare 的运行日志
- `src/`、`tests/`、`docs/`
- Unity 工程真实资产与 `<animation-root>` 业务输出

## 健康检查

```powershell
Invoke-RestMethod http://127.0.0.1:7865/health
```

## 任务中心完整重发

任务中心的“完整重发”只对已结束的图片/序列帧和视频直发任务开放。操作会：

1. 自动锁定任务保存的原飞书会话，不允许在 UI 临时改收件人；
2. 在后台重新生成一个 ZIP，不复用“历史已交付”判断；
3. 视频包包含原视频、抽帧、透明抠图和后处理；
4. 图片/序列帧包包含原始帧、透明抠图和后处理；
5. 没有角色参考资料时仍交付原始素材与透明抠图，并在后处理目录附 README；
6. 单独记录排队、打包、上传、成功或失败，不覆盖原任务的完成状态和交付历史。

按钮重复点击会被后台请求 ID 和 worker PID 双重拦截。重发进程日志位于
`storage/logs/task_redelivery_<RUN_ID>.log`。若 WebUI 报 `invalid X-Skill-Token`，
确认根目录 `.env` 的 `SKILL_API_TOKEN` 后重启 WebUI；Vite 代理会从同一文件注入令牌。

任务中心只在存在运行中或等待中的父任务时展示统一队列，空闲时不重复占用空间。
列表支持按来源、状态和关键字筛选；完整重发使用站内确认弹窗，不再调用浏览器原生
确认框。重发成功或失败信息通过按钮状态和悬浮说明表达，不额外增加一行，避免任务列
错位。中等宽度窗口使用两层自适应布局，任务列表不会产生横向滚动；任务行没有整行
hover 变色，滚轮滚动时不会连续闪烁。

## ImageClip 工作流运行时兼容检查

每次使用默认 ImageClip 工作流启动图片或视频直发前，系统除同步 Git 资源外，还会读取
秋叶 ComfyUI 的 `/object_info`，核对所有启用节点类型是否已注册。检查失败发生在创建任务
之前，因此不会占用 GPU，也不会把一个实际未启动的任务轻易标成处理失败。

若返回类似：

```text
工作流包含秋叶未注册的节点：CodexFootRegionPasteOriginalShapeV4。
请补齐 ImageClip 自定义节点并重启秋叶。
```

说明工作流 JSON 已引用该节点，但 ImageClip 仓库或秋叶运行时尚未包含对应 Python 实现。
此时等待上游更新并重启秋叶；不要重复提交用户素材。更新后先执行：

```powershell
Invoke-RestMethod http://127.0.0.1:8188/system_stats
Invoke-RestMethod http://127.0.0.1:8188/queue
```

然后运行 `matting_pipeline.verify`，确认 `missing_node_types` 为空，再进行真实单图 smoke test。

## 查看日志

```powershell
Get-Content logs\conversation.log -Tail 80 -Wait
Get-Content logs\feishu_ws.log -Tail 50
Get-Content logs\gateway.log -Tail 50
```

## 安全合规说明

**禁止使用**：Cloudflare Tunnel、ngrok、frp、Tailscale、ZeroTier、反向 SSH 或任何内网穿透工具。  
**原因**：公司内网安全合规要求，禁止将内网服务暴露到互联网。  
**替代方案**：飞书官方长连接（WebSocket），本地主动连接飞书，无需任何公网暴露。

## 飞书后台配置（长连接模式）

1. 进入飞书开放平台 → 应用 → 事件与回调 → 事件配置
2. 订阅方式选择：**使用长连接接收事件**（不需要填写 URL）
3. 添加事件：`im.message.receive_v1`
4. 开通：消息接收和发送权限
5. 发布应用版本

## 测试命令

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\test_llm_proxy.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\test_feishu_ws_config.ps1
conda run -n assetclaw python -m pytest
```

## 独立 Unity 工具

以下能力已经接入飞书 skills，但不属于完整动画自动化 6 步主流程：

- `unity_tools.atlas_status`：读取 `Assets/TATest/AtlasSizeReport.json`
- `unity_tools.atlas_report`：调用 Unity `SpriteAtlasGeneratorTool.DoGenerate()` 生成图集大小报告，需要确认
- `unity_tools.rename_preview`：预览 `AnimTextureBatchRename` 贴图命名整理，不落地
- `unity_tools.rename_run`：执行 `AnimTextureBatchRename`，需要确认

示例飞书指令：

```text
查看图集大小报告
生成图集大小报告
预览动画贴图批量重命名 Assets/Art/UI/SpritesAnim/Emoji/Mia/Common Assets/Art/UI/Animation/Emoji/Mia
执行动画贴图批量重命名 Assets/Art/UI/SpritesAnim/Emoji/Mia/Common Assets/Art/UI/Animation/Emoji/Mia
```
