# Git 仓库与运行数据管理

## 追踪边界

Git 只保存源代码、测试、文档和必要的静态资源。以下目录是本机运行状态，只保留根目录的 `.gitkeep`：

- `data/`：SQLite 数据库、WAL 和审计备份
- `logs/`：运行日志
- `storage/`：飞书下载、图片/视频任务、ComfyUI/Cherry 输出和状态文件
- `.pytest-tmp*/`、`.pytest_cache/`：测试临时文件
- `external_webui/dist/`：Vite 构建结果

这些文件被 `.gitignore` 排除。清理 Git 索引不会删除本机文件：

```powershell
git rm -r --cached --ignore-unmatch storage data logs .pytest_cache
git rm -r --cached --ignore-unmatch ".pytest-tmp*" external_webui/dist .idea
git add -f storage/.gitkeep data/.gitkeep logs/.gitkeep
```

## 提交前检查

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\check_repo_hygiene.ps1
git diff --check
git status --short
```

检查脚本会拒绝：运行目录中的已追踪文件、测试临时文件、前端构建结果、IDE 状态以及超过 20 MiB 的单个已追踪文件。确实需要的大型静态资源应先评估 Git LFS，而不是直接提高阈值。

## 安全清理

`clean_project.ps1` 默认只预览，不删除任何文件：

```powershell
# 预览
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\clean_project.ps1

# 清理 Python/pytest 缓存和废弃隧道文件
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\clean_project.ps1 -Apply

# 额外清理可重建的运行缓存；不会删除数据库、任务输入输出和业务资产
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\clean_project.ps1 -Apply -IncludeRuntimeCaches
```

## 已经推送的误提交

后续清理提交只能让这些文件从当前分支消失，旧提交中的对象仍保留在 Git 历史里。彻底减小 `.git` 需要重写历史并强制推送，这会改变所有提交哈希，必须单独安排维护窗口并通知所有协作者；不要在日常清理脚本中自动执行。

Windows 下中文路径显示为八进制转义时，可临时使用：

```powershell
git -c core.quotepath=false status
```
