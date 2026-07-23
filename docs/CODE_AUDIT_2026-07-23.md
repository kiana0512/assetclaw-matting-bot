# 代码与仓库审计（2026-07-23）

## 已完成

- Git 索引：移除 `storage/`、`data/`、`logs/`、`.pytest-tmp*/`、`.pytest_cache/`、`external_webui/dist/` 和 `.idea/` 中的运行/生成文件；本地数据库、日志和任务业务数据均保留。
- 忽略规则：运行目录改为默认全部忽略，只允许三个根 `.gitkeep`；补充 pytest、IDE、Vite 构建和常见临时文件规则。
- 自动检查：新增 `scripts/check_repo_hygiene.ps1`，检查运行产物、编辑器状态、生成目录和超大追踪文件。
- 安全清理：`scripts/clean_project.ps1` 改为默认 dry-run，只有 `-Apply` 才删除；路径必须解析在仓库内部；数据库、任务输入输出和业务资产永不纳入默认清理。
- 重复/废弃文件：删除两个完全重复的 Cherry 工具副本、IDE 工程状态、异常的根目录 `-File` 和已经禁用的公网隧道启动脚本。
- 配置可移植性：普通 `Settings(...)` 不再偷读真实部署 `.env`；只有进程全局 `settings` 加载 `.env`。外部 ComfyUI 路径优先发现与当前 checkout 同级的部署，避免误用当前用户桌面的另一套环境。
- 静态检查：184 个 Python 文件完成无落盘语法编译；追踪文件中未发现常见凭据字面量；`git diff --check` 通过。
- 前端：Vite 生产构建通过。
- Git 完整性：`git fsck --full --no-reflogs` 通过，没有缺失或损坏对象；仅报告不可达的 dangling blob/tree。对象库另有约 2.94 MiB 的中断操作临时垃圾，不影响仓库完整性，本次不冒险手删 `.git/objects`。

## 测试结果

- 全量：`309 passed, 18 failed`。
- 配置与路径可移植性修复后：`11 passed`。
- 18 个剩余失败不是本次清理引入，集中在以下旧契约：
  - 3 个 `agent.current_work` 用例仍要求旧表格/任务 ID 文案，当前实现已切换为面向飞书的紧凑父任务视图。
  - 2 个文件技能用例硬编码 `E:\`，与当前 C 盘 checkout 和安全白名单冲突。
  - 1 个语音路由用例要求拆成两个 tool call，当前路由已合并到 `agent.task_overview`。
  - 12 个 P4 用例仍针对已退役的 `ai_art_comfyui` 配置以及当前 `P4Operations` 不再提供的 inventory/setup/compare API；仓库现行示例是 `spark_client_ui` 的 shelve-only 配置。

这些失败应通过确认“现行产品契约”后更新测试或恢复功能，不能为了全绿而盲目回退生产行为。

## 仍需单独授权的事项

误提交已经推送到 `main`，因此旧二进制对象仍在 Git 历史，当前 `.git` 约 821 MiB。彻底瘦身需要 `git filter-repo`/同类历史重写和强制推送，会改变提交哈希并影响其他协作者。本次只生成安全的后续清理变更，不自动重写历史、不提交、不推送。

`data/assetclaw.pre_audit_cleanup.20260723.db` 是约 8.9 GiB 的数据库回滚备份。本次保留；删除它会失去回滚点，需要单独确认。
