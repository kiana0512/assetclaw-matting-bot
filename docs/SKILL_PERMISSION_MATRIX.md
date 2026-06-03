# Skill Permission Matrix

## 风险等级说明

| 级别 | 含义 |
|------|------|
| `readonly` | 无写操作，无副作用，所有用户可用 |
| `write_safe` | 写入 DB 或创建文件，低风险，审计记录 |
| `write_caution` | 移动/重命名，中风险，审计记录 |
| `danger_confirm` | 删除、清空等高风险动作，必须二次确认 |
| `egress_caution` | 向当前飞书会话发送允许路径内的本地文件，审计记录，不默认二次确认 |
| `dangerous_blocked` | 永久禁止，代码中不实现 |

## 允许/禁止路径

- **允许根路径**：`ALLOWED_ROOTS`（默认 `D:\`、`E:\`、`F:\`、`Z:\`、`\\audioshare.lilith.com\AIart\公共机共享\抠图`）
- **禁止根路径**：`C:\`
- **永久禁止关键词**：`.env`、`.ssh`、`AppData`、`Windows`、`Program Files`、`ProgramData`、`$Recycle.Bin`、`System Volume Information`

---

## 系统 / 帮助类 Skills

| Skill | Domain | Risk | Impl | Fake | Confirm | 说明 |
|-------|--------|------|------|------|---------|------|
| `bot.help` | bot | readonly | Yes | No | No | 显示机器人能力和使用示例 |
| `bot.skills` | bot | readonly | Yes | No | No | 列出所有技能及状态 |
| `bot.permissions` | bot | readonly | Yes | No | No | 显示安全边界和允许/禁止路径 |
| `bot.status` | bot | readonly | Yes | No | No | Gateway/LLM/DB/WS 模式状态 |
| `bot.errors` | bot | readonly | Yes | No | No | 最近 10 条错误记录 |

**自然语言示例**：
```
你会做什么
帮助
查看技能列表
查看权限说明
查看当前系统状态
查看最近错误
```

---

## 文件系统类 Skills

| Skill | Domain | Risk | Impl | Fake | Confirm | 说明 |
|-------|--------|------|------|------|---------|------|
| `file.list_allowed` | file | readonly | Yes | No | No | 列出目录内容（仅元信息） |
| `file.exists` | file | readonly | Yes | No | No | 检查路径是否存在 |
| `file.info` | file | readonly | Yes | No | No | 查看文件/目录元信息（不读取内容） |
| `file.find_name` | file | readonly | Yes | No | No | 按文件名模式搜索 |
| `file.tree` | file | readonly | Yes | No | No | 列目录树（深度限制） |
| `file.recent` | file | readonly | Yes | No | No | 最近修改文件 |
| `file.list_by_type` | file | readonly | Yes | No | No | 按类型/扩展名列文件 |
| `file.copy` | file | write_safe | Yes | No | No | 复制文件到允许路径 |
| `file.copy_as` | file | write_safe | Yes | No | No | 复制并指定新文件名 |
| `file.duplicate_same_dir` | file | write_safe | Yes | No | No | 在原目录复制一份 |
| `file.move` | file | write_caution | Yes | No | Yes | 移动/重命名文件 |
| `file.mkdir` | file | write_safe | Yes | No | No | 创建目录 |
| `file.zip_paths` | file | write_caution | Yes | No | No | 打包 zip |
| `file.read_text` | file | readonly | Yes | No | No | 读取小文本文件 |
| `file.write_text` | file | write_safe | Yes | No | No | 写入文本文件 |
| `file.append_text` | file | write_safe | Yes | No | No | 追加文本 |
| `file.hash` | file | readonly | Yes | No | No | 计算文件 hash |
| `file.batch_info` | file | readonly | Yes | No | No | 批量路径信息 |
| `file.copy_tree` | file | write_safe | Yes | No | No | 跨盘复制目录树 |
| `file.copy_many` | file | write_safe | Yes | No | No | 批量复制文件 |
| `file.move_many` | file | write_caution | Yes | No | Yes | 批量移动文件 |
| `file.mkdir_many` | file | write_safe | Yes | No | No | 批量创建目录 |
| `file.rename_many` | file | write_caution | Yes | No | Yes | 按明确映射批量重命名 |
| `file.rename_sequence` | file | write_caution | Yes | No | Yes | 按顺序批量重命名文件 |
| `file.unzip` | file | write_caution | Yes | No | Yes | 解压 zip |
| `file.search_text` | file | readonly | Yes | No | No | 搜索允许目录内文本内容 |
| `file.preview` | file | readonly | Yes | No | No | 预览文本或二进制文件头 |
| `file.count` | file | readonly | Yes | No | No | 统计目录文件类型 |
| `file.manifest` | file | write_safe | Yes | No | No | 导出目录文件清单 |
| `archive.list` | archive | readonly | Yes | No | No | 查看 zip 内容，不解压 |
| `json.query` | file | readonly | Yes | No | No | 查询 JSON 内容 |
| `csv.summary` | file | readonly | Yes | No | No | 预览 CSV/TSV 列和样例行 |
| `file.delete` | file | danger_confirm | Yes | No | Yes | 删除文件/目录 |
| `file.empty_dir` | file | danger_confirm | Yes | No | Yes | 清空目录 |

**Allowed roots**：`ALLOWED_ROOTS=D:;E:;F:;Z:;\\audioshare.lilith.com\AIart\公共机共享\抠图`  
**Denied patterns**：`.env`, `.ssh`, `AppData`, `Windows`, `Program Files`, `ProgramData`, `$Recycle.Bin`, `System Volume Information`

**自然语言示例**：
```
看看 E 盘有哪些文件
列出 E:\assetclaw-matting-bot\storage 下的文件
E:\assetclaw-matting-bot\README.md 是否存在
查看 E:\assetclaw-matting-bot\README.md 的信息
搜索文件名包含 batch 的文件
显示 E:\assetclaw-matting-bot 目录树
查看最近 24 小时内修改的文件
列出 E:\ 下面的图片文件
把 E:\a.png 复制一份并改名为 a_bak.png
把 E:\a.png 在原路径复制一份，后缀加 _bak
把 E:\assetclaw-matting-bot\README.md 复制到 E:\assetclaw-matting-bot\storage\README_bak.md
把 E:\assetclaw-matting-bot\storage\README_bak.md 移动到 E:\assetclaw-matting-bot\storage\README_moved.md
创建目录 E:\assetclaw-matting-bot\storage\feishu_test_folder
查看 D E F 盘空间
把 D:\assets 复制到 F:\backup\assets
把刚才列出的图片按顺序改名为 1 2 3 4 5
计算 F:\package.zip 的 sha256
删除 F:\temp\old.png
```

---

## 工作盘 Skills

| Skill | Domain | Risk | Impl | Fake | Confirm | 说明 |
|-------|--------|------|------|------|---------|------|
| `workspace.roots` | workspace | readonly | Yes | No | No | 查看允许访问根路径 |
| `workspace.disk_usage` | workspace | readonly | Yes | No | No | 查看 D/E/F 磁盘容量 |

---

## 图片 / 媒体 / 飞书发送 Skills

| Skill | Domain | Risk | Impl | Fake | Confirm | 说明 |
|-------|--------|------|------|------|---------|------|
| `image.list` | image | readonly | Yes | No | No | 列图片文件 |
| `image.info` | image | readonly | Yes | No | No | 查看图片尺寸、格式、模式 |
| `image.batch_info` | image | readonly | Yes | No | No | 批量图片元信息 |
| `image.ocr` | image | readonly | Yes | No | No | 提取图片中文字 |
| `image.convert_format` | image | write_safe | Yes | No | No | 图片格式转换 |
| `image.resize` | image | write_safe | Yes | No | No | 图片缩放 |
| `feishu.send_file` | feishu | egress_caution | Yes | No | No | 上传允许路径内的本地文件并发送到当前飞书会话 |
| `feishu.send_file_by_name` | feishu | egress_caution | Yes | No | No | 按文件名或省略名查找并发送到当前飞书会话 |
| `feishu.send_image` | feishu | egress_caution | Yes | No | No | 上传允许路径内的本地图片并以图片形式发送 |
| `feishu.send_image_by_name` | feishu | egress_caution | Yes | No | No | 按文件名查找图片并以图片形式发送 |
| `feishu.zip_and_send` | feishu | egress_caution | Yes | No | No | 打包允许路径内的文件/目录并发送到当前飞书会话 |
| `translate.text` | translate | readonly | Yes | No | No | 翻译文本，返回自然语言译文 |
| `translate.image_text` | translate | readonly | Yes | No | No | 识别图片中文字并翻译 |

**自然语言示例**：
```
看看 E 盘有哪些图片
查看 E:\a.png 的图片尺寸
把 E:\assetclaw-matting-bot\README.md 通过飞书发给我
把 E 盘里 img_v3_02125_53d2b164...608g.png 发给我
把共享盘 input 文件夹压缩成 zip 并发送给我
```

---

## 记忆类 Skills

| Skill | Domain | Risk | Impl | Fake | Confirm | 说明 |
|-------|--------|------|------|------|---------|------|
| `memory.remember` | memory | write_safe | Yes | No | No | 保存键值备忘到 SQLite |
| `memory.list` | memory | readonly | Yes | No | No | 查询备忘记录 |

**自然语言示例**：
```
记住：项目名称是 AssetClaw
查看记忆列表
```

---

## 抠图批次 Skills（fake mode）

> 当前全部为 fake mode，不会真正运行 GPU / ComfyUI。批次状态变化只写入 SQLite。

| Skill | Domain | Risk | Impl | Fake | Confirm | 说明 |
|-------|--------|------|------|------|---------|------|
| `matting.batch_create` | matting | write_safe | Yes | Yes | No | 创建批次（计数，不运行 GPU） |
| `matting.batch_start` | matting | write_safe | Yes | Yes | No | 启动批次（改状态） |
| `matting.batch_status` | matting | readonly | Yes | Yes | No | 查询批次状态 |
| `matting.batch_list` | matting | readonly | Yes | Yes | No | 列出所有批次 |
| `matting.batch_detail` | matting | readonly | Yes | Yes | No | 批次详情 |
| `matting.batch_pause` | matting | write_safe | Yes | Yes | No | 暂停批次 |
| `matting.batch_resume` | matting | write_safe | Yes | Yes | No | 恢复批次 |
| `matting.batch_cancel` | matting | write_safe | Yes | Yes | No | 取消批次 |
| `matting.shared_start` | matting | write_safe | Yes | No | No | 共享盘输入同步到本地，跑 ComfyUI，再同步输出回共享盘 |
| `matting.shared_status` | matting | readonly | Yes | No | No | 查询共享盘抠图任务进度、目录、ETA、GPU |
| `matting.shared_sync_outputs` | matting | write_safe | Yes | No | No | 手动同步本地输出回共享盘 |

**自然语言示例**：
```
用 E:\assetclaw-matting-bot\storage\batch_inputs 创建一个抠图批次
列出所有抠图批次
查看批次 BATCH_XXXXXXXXXXXX 的状态
启动批次 BATCH_XXXXXXXXXXXX
```

---

## 队列 / ComfyUI Stub Skills

| Skill | Domain | Risk | Impl | Fake | Confirm | 说明 |
|-------|--------|------|------|------|---------|------|
| `queue.status` | queue | readonly | Yes (stub) | Yes | No | 队列状态 stub |
| `comfyui.status` | comfyui | readonly | Yes | No | No | ComfyUI fake/real mode、URL、工作流、连通性 |
| `comfyui.workflows` | comfyui | readonly | Yes | No | No | 列出 workflow json |
| `comfyui.workflow_info` | comfyui | readonly | Yes | No | No | 查看 workflow 节点和输入输出节点 |
| `comfyui.workflow_select` | comfyui | write_safe | Yes | No | No | 为当前对话选择默认 workflow |
| `comfyui.run_preview` | comfyui | readonly | Yes | No | No | 启动前预览 workflow、输入输出目录、图片总数和关键节点 |
| `comfyui.queue_status` | comfyui | readonly | Yes | No | No | 查询 ComfyUI 原生 `/queue` |
| `comfyui.run_start` | comfyui | write_safe | Yes | No | Yes | 指定 workflow/input/output 递归启动图片管线并推送进度 |
| `comfyui.run_status` | comfyui | readonly | Yes | No | No | 查看管线进度、ETA、输入输出、GPU |
| `comfyui.run_list` | comfyui | readonly | Yes | No | No | 查看当前活跃任务；可按参数包含历史任务 |
| `comfyui.run_update` | comfyui | write_safe | Yes | No | No | 修改排队或暂停任务的 workflow/input/output |
| `comfyui.run_pause` | comfyui | write_safe | Yes | No | No | 暂停后续图片提交 |
| `comfyui.run_resume` | comfyui | write_safe | Yes | No | No | 继续暂停的任务 |
| `comfyui.run_cancel` | comfyui | write_caution | Yes | No | Yes | 终止任务并尝试中断 ComfyUI 队列 |
| `comfyui.run_delete` | comfyui | write_safe | Yes | No | No | 删除/归档已结束、失败、已取消的任务记录 |
| `comfyui.run_sync_outputs` | comfyui | write_safe | Yes | No | No | 下载完成输出到指定输出目录 |
| `cherry.info` | cherry | readonly | Yes | No | No | 查看 Cherry 工具状态和默认参数 |
| `cherry.run_preview` | cherry | readonly | Yes | No | No | 预览 Cherry 帧序列处理任务 |
| `cherry.run_start` | cherry | write_safe | Yes | No | Yes | 启动 Cherry 平滑/缩放/锐化任务，同结构输出 |
| `cherry.run_status` | cherry | readonly | Yes | No | No | 查看 Cherry 任务进度、ETA、GPU |
| `cherry.run_list` | cherry | readonly | Yes | No | No | 列出 Cherry 任务 |
| `cherry.run_cancel` | cherry | write_safe | Yes | No | No | 终止 Cherry 任务 |
| `cherry.run_delete` | cherry | write_safe | Yes | No | No | 删除/归档 Cherry 任务记录 |
| `frame.info` | frame | readonly | Yes | No | No | 查看飞书抽帧工具配置 |
| `frame.run_preview` | frame | readonly | Yes | No | No | 预览飞书表格视频下载和抽帧任务 |
| `frame.run_start` | frame | write_safe | Yes | No | Yes | 下载飞书视频附件并抽 PNG 序列帧 |
| `frame.run_status` | frame | readonly | Yes | No | No | 查看抽帧任务进度 |
| `frame.run_list` | frame | readonly | Yes | No | No | 列出抽帧任务 |
| `frame.run_cancel` | frame | write_safe | Yes | No | No | 终止抽帧任务 |
| `pipeline.run_preview` | pipeline | readonly | Yes | No | No | 预览完整动画自动化流程 |
| `pipeline.run_start` | pipeline | write_safe | Yes | No | Yes | 执行抽帧 -> 抠图 -> 平滑三步流程 |
| `pipeline.run_status` | pipeline | readonly | Yes | No | No | 查看完整流程和子任务进度 |
| `pipeline.run_list` | pipeline | readonly | Yes | No | No | 列出完整流程任务 |
| `pipeline.run_cancel` | pipeline | write_safe | Yes | No | No | 终止完整流程和当前子任务 |
| `system.gpu_status` | system | readonly | Yes | No | No | nvidia-smi GPU 显存/利用率/温度/功耗 |
| `system.process_status` | system | readonly | Yes | No | No | 查询匹配进程状态 |

---

## 已保留/未实现 Skills

| Skill | Domain | Risk | Impl | 说明 |
|-------|--------|------|------|------|
| `log.tail` | logs | readonly | No | 预留，未实现 |
| `system.health` | system | readonly | No | 预留，可扩展 |

---

## 永久禁止的操作

以下操作在 Skill Registry 中不存在，也不会被实现：

| 操作 | 原因 |
|------|------|
| shell exec | 安全红线，任意代码执行 |
| format disk / partition / change drive letter | 极高危，永久禁止 |
| C:\ access | 系统盘，不开放 |
| unrestricted file.read_content | 防止密钥泄漏；只开放安全文本扩展名的 `file.read_text` |
| 启动 cloudflared | 内网穿透，违规 |
| 启动 ngrok/frp/Tailscale | 内网穿透，违规 |
| 监听 0.0.0.0 作为飞书入口 | 暴露内网 |
