# Skills Guide

机器人只能通过 skills 操作本机和指定共享盘。默认允许路径来自 `ALLOWED_ROOTS`，当前默认是 `D:`、`E:`、`F:`、映射共享盘 `Z:`，以及 `\\audioshare.lilith.com\AIart\公共机共享\抠图`。`C:` 不开放。`.env`、`.ssh`、`Windows`、`AppData`、`Program Files`、`ProgramData`、`$Recycle.Bin`、`System Volume Information` 永久拒绝访问。

共享盘可以列目录、复制、同步文件；只是抠图任务不直接在共享盘上计算，会先同步到本地工作区，跑完再同步回共享盘。

当前共享盘映射：

```text
Z:\ = \\audioshare.lilith.com\AIart
Z:\公共机共享\抠图 = \\audioshare.lilith.com\AIart\公共机共享\抠图
```

## 回复风格

- 飞书里只发关键结果，不刷长解释。
- 本地 log 仍记录完整链路、工具入参和结果。
- 需要二次确认的操作会明确给确认码。
- 对话记忆会自动压缩：保留近期原文，旧对话写入摘要并清掉旧原文，避免上下文越来越大；飞书里会有一条简短提示。

## 已实现 Skills

| Skill | 用途 | 风险 |
|---|---|---|
| `bot.help` | 简短帮助 | readonly |
| `bot.skills` | 查看技能清单 | readonly |
| `bot.permissions` | 查看安全边界 | readonly |
| `bot.status` | 查看系统状态 | readonly |
| `bot.errors` | 查看最近错误 | readonly |
| `file.list_allowed` | 列目录 | readonly |
| `file.exists` | 检查路径是否存在 | readonly |
| `file.info` | 查看文件/目录元信息 | readonly |
| `file.find_name` | 按文件名搜索 | readonly |
| `file.tree` | 查看目录树 | readonly |
| `file.recent` | 查看最近修改文件 | readonly |
| `file.list_by_type` | 按类型列文件：图片、视频、表格、压缩包 | readonly |
| `file.copy` | 复制文件到指定路径 | write_safe |
| `file.copy_as` | 复制文件并指定新文件名 | write_safe |
| `file.duplicate_same_dir` | 在原目录复制一份并加后缀 | write_safe |
| `file.mkdir` | 创建目录 | write_safe |
| `file.move` | 移动或重命名 | write_caution，需确认 |
| `file.zip_paths` | 打包 zip | write_caution |
| `workspace.roots` | 查看允许访问的工作盘 | readonly |
| `workspace.disk_usage` | 查看 D/E/F 磁盘空间 | readonly |
| `file.read_text` | 读取允许路径内的小文本文件 | readonly |
| `file.write_text` | 写入文本文件 | write_safe |
| `file.append_text` | 追加文本 | write_safe |
| `file.hash` | 计算文件 hash | readonly |
| `file.batch_info` | 批量查询路径元信息 | readonly |
| `file.copy_tree` | 复制整个目录树 | write_safe |
| `file.copy_many` | 批量复制文件 | write_safe |
| `file.move_many` | 批量移动文件 | write_caution，需确认 |
| `file.mkdir_many` | 批量创建目录 | write_safe |
| `file.rename_many` | 按明确映射批量重命名 | write_caution，需确认 |
| `file.rename_sequence` | 按顺序批量重命名文件 | write_caution，需确认 |
| `file.unzip` | 解压 zip 到指定目录 | write_caution，需确认 |
| `file.search_text` | 在允许目录内搜索文本内容 | readonly |
| `file.preview` | 预览文本文件或二进制文件头 | readonly |
| `file.count` | 统计目录中文件/图片/视频/表格/压缩包数量 | readonly |
| `file.manifest` | 导出目录文件清单到 JSON/CSV | write_safe |
| `archive.list` | 查看 zip 内文件，不解压 | readonly |
| `json.query` | 查询 JSON 文件或 JSON Pointer | readonly |
| `csv.summary` | 预览 CSV/TSV 列和样例行 | readonly |
| `file.delete` | 删除文件/目录 | danger_confirm，需确认 |
| `file.empty_dir` | 清空目录 | danger_confirm，需确认 |
| `image.list` | 列图片文件 | readonly |
| `image.info` | 查看图片尺寸/格式 | readonly |
| `image.batch_info` | 批量查看图片尺寸/格式 | readonly |
| `image.ocr` | 提取图片中文字 | readonly |
| `image.convert_format` | 图片格式转换 | write_safe |
| `image.resize` | 图片缩放 | write_safe |
| `feishu.send_file` | 把本地文件发到当前飞书会话 | egress_caution |
| `feishu.send_file_by_name` | 按文件名或省略名查找并发到当前飞书会话 | egress_caution |
| `feishu.send_image` | 把本地图片以飞书图片形式发回，可直接预览 | egress_caution |
| `feishu.send_image_by_name` | 按文件名查找图片并以飞书图片形式发回 | egress_caution |
| `feishu.zip_and_send` | 打包文件/目录后立刻发到当前飞书会话 | egress_caution |
| `translate.text` | 翻译文本，输出自然语言译文 | readonly |
| `translate.image_text` | 识别图片中文字并翻译 | readonly |
| `speech.transcribe` | 把本地音频或飞书语音附件转文字，默认 FunASR/SenseVoiceSmall | readonly |
| `speech.synthesize` | 把文字合成为本地语音文件，默认 IndexTTS2 | write_safe |
| `speech.send_tts` | 合成语音并发送到当前飞书会话 | egress_caution |

## 多模态输入

飞书发来的图片、视频和普通文件会保存到：

```text
storage\feishu_inbox\日期\会话\
```

保存后可以继续引用“刚刚那张图/视频/文件”做处理：

```text
保存到 E:\images
查看这张图的尺寸
预览发回给我
分析这张图里有什么
```

图片支持保存、复制、查尺寸、转格式、缩放、飞书图片形式发回。视频先支持保存、文件信息、复制、移动、发回。

## 语音输入和语音回复

飞书语音消息会被保存为音频附件，再由 `speech.transcribe` 转文字。识别出来的文字会重新进入 Agent 路由，所以语音可以触发多个工具调用，例如：

```text
查看 GPU 状态和当前任务
开始飞书抽帧，下载到 E:\raw_videos，抽帧输出 E:\output_frames
把刚刚那张图里的文字翻译成中文
```

语音回复由会话模式控制：

```text
开启语音回复
关闭语音回复
只发文字
```

开启后，机器人仍然先发文字结果，然后再用 TTS 合成语音并补发文件。ASR 默认走本地 FunASR / SenseVoiceSmall；TTS 默认走本地 IndexTTS2。如果本地模型首次加载，飞书会先提示预计等待时间。

## 唱歌和接歌词

唱歌模式、接歌词、续下一句功能已经关闭。飞书里说“进入唱歌模式”“陪我唱歌”“不要再接下一句了”时，机器人只会提示该功能已关闭，并继续按正常聊天或工具指令处理。

当前保留的能力是：可以搜索公开网页、定位歌词来源、解释用户贴出的短句含义；不会进入唱歌模式，不会续写原歌下一句，也不会生成“原创下一句”。

## 人工确认

所有 destructive 或高风险操作会先进入 `pending_confirmation`，不会直接执行。常见包括：

- 删除、清空目录、移动、批量重命名、解压覆盖
- 启动 ComfyUI/Cherry/抽帧/完整 pipeline 等长任务
- 动画流程重跑、归档旧目录、从视频重建帧
- P4 submit/revert/sync 等会改变工作区或服务器状态的动作

确认流程：

```text
用户：开始批量抠图 E:\input 到 E:\output
机器人：需要确认：comfyui.run_start
回复：确认执行 CONFIRM_xxxxx
用户：确认执行 CONFIRM_xxxxx
机器人：开始执行，并在飞书推送进度
```

未确认前只会保存待确认记录；确认码过期或参数不匹配时需要重新发起任务。
| `memory.remember` | 保存本地记忆 | write_safe |
| `memory.list` | 查看本地记忆 | readonly |
| `matting.batch_create` | 创建抠图批次（fake mode） | write_safe |
| `matting.batch_start` | 启动批次（fake mode） | write_safe |
| `matting.batch_status` | 查看批次状态 | readonly |
| `matting.batch_list` | 列批次 | readonly |
| `matting.batch_detail` | 查看批次详情 | readonly |
| `matting.batch_pause` | 暂停批次（fake mode） | write_safe |
| `matting.batch_resume` | 恢复批次（fake mode） | write_safe |
| `matting.batch_cancel` | 取消批次（fake mode） | write_safe |
| `matting.shared_start` | 共享盘输入 staging 到本地，跑 ComfyUI，再同步回共享盘 | write_safe |
| `matting.shared_status` | 查询共享盘抠图任务进度、目录、ETA、GPU | readonly |
| `matting.shared_sync_outputs` | 手动把本地输出同步回共享盘输出目录 | write_safe |
| `queue.status` | 队列状态 stub | readonly |
| `comfyui.status` | ComfyUI fake/real mode、URL、工作流、连通性 | readonly |
| `comfyui.workflows` | 列出本地 ComfyUI workflow json | readonly |
| `comfyui.workflow_info` | 查看 workflow 节点、LoadImage、SaveImage 信息 | readonly |
| `comfyui.workflow_select` | 为当前对话选择默认 workflow | write_safe |
| `comfyui.run_preview` | 启动前预览 workflow、输入输出目录、图片总数和关键节点 | readonly |
| `comfyui.queue_status` | 查询 ComfyUI 原生 `/queue` | readonly |
| `comfyui.run_start` | 按 workflow/input/output 递归启动 ComfyUI 图片任务，可保留目录结构并安静推送进度，启动前需确认 | write_safe |
| `comfyui.run_status` | 查看当前/指定 ComfyUI 管线进度、ETA、GPU | readonly |
| `comfyui.run_list` | 查看当前活跃的 ComfyUI 任务；可按参数包含历史任务 | readonly |
| `comfyui.run_update` | 修改排队或暂停任务的 workflow/input/output | write_safe |
| `comfyui.run_pause` | 暂停后续图片提交 | write_safe |
| `comfyui.run_resume` | 继续暂停的任务 | write_safe |
| `comfyui.run_cancel` | 终止任务并尝试中断 ComfyUI 队列 | write_caution |
| `comfyui.run_delete` | 删除/归档已结束、失败、已取消的任务记录 | write_safe |
| `comfyui.run_sync_outputs` | 把完成的 ComfyUI 输出下载到指定输出目录 | write_safe |
| `cherry.info` | 查看 Cherry 帧序列工具路径、可用状态和默认参数 | readonly |
| `cherry.run_preview` | 启动前预览 Cherry 平滑/缩放/锐化任务 | readonly |
| `cherry.run_start` | 对输入目录递归做 Cherry 帧序列处理，并同结构输出，启动前需确认 | write_safe |
| `cherry.run_status` | 查看 Cherry 任务进度、ETA、输入输出、GPU | readonly |
| `cherry.run_list` | 查看当前活跃的 Cherry 平滑任务；可按参数包含历史任务 | readonly |
| `cherry.run_cancel` | 终止 Cherry 任务 | write_safe |
| `cherry.run_delete` | 删除/归档已结束、失败、已取消的 Cherry 任务记录 | write_safe |
| `frame.info` | 查看飞书抽帧工具配置、fps、输入输出目录 | readonly |
| `feishu_table.export_json` | 将飞书多维表格导出为精简业务 JSON：角色、状态、类型、附件文件名、建议目录 | write_safe |
| `feishu_table.restore_plan` | 基于 raw 调试导出生成写回飞书的 dry-run 计划，不直接写表 | readonly |
| `frame.run_preview` | 预览飞书表格视频下载 + 抽帧任务 | readonly |
| `frame.run_start` | 下载飞书表格视频附件并抽 PNG 序列帧，启动前需确认 | write_safe |
| `frame.run_status` | 查看抽帧任务进度 | readonly |
| `frame.run_list` | 查看抽帧任务列表 | readonly |
| `frame.run_cancel` | 终止抽帧任务 | write_safe |
| `animation_flow.preview` | 预览完整 7 步动画自动化流程 | readonly |
| `animation_flow.start` | 执行飞书下载 -> 抽帧 -> 抠图 -> Cherry -> unity_ready -> Unity 导入 -> P4 Shelve-only，启动前需确认 | write_safe |
| `animation_flow.status` | 查看完整 7 步流程和子任务进度 | readonly |
| `animation_flow.list` | 查看完整 7 步流程任务列表 | readonly |
| `animation_flow.cancel` | 终止完整 7 步流程及当前子任务 | write_safe |
| `unity_ready.preview` | 预览 unity_ready scene/emoji 输出结构 | readonly |
| `unity_ready.build` | 单独生成 unity_ready，启动前需确认 | write_safe |
| `unity_ready.status` | 查看 unity_ready JSON 和帧数量 | readonly |
| `unity_import.preview` | 预览 Unity 插件导入路径 | readonly |
| `unity_import.run` | 调用 Unity 插件 API 导入引擎，启动前需确认 | write_safe |
| `unity_import.status` | 查看 Unity 导入准备状态 | readonly |
| `pipeline.run_preview` | 预览旧三步动画流程 | readonly |
| `pipeline.run_start` | 旧入口：固定顺序执行抽帧 -> ComfyUI 抠图 -> Cherry 平滑，启动前需确认 | write_safe |
| `pipeline.run_status` | 查看旧三步流程和子任务进度 | readonly |
| `pipeline.run_list` | 查看旧三步流程任务列表 | readonly |
| `pipeline.run_cancel` | 终止旧三步流程及当前子任务 | write_safe |
| `system.gpu_status` | 查询 nvidia-smi GPU 显存/利用率/温度/功耗 | readonly |
| `system.process_status` | 查询匹配进程状态 | readonly |

## 常用说法

```text
看看 E 盘有哪些文件
看看 E 盘有哪些图片
递归查看 E:\assetclaw-matting-bot 里的图片
找出 E:\assetclaw-matting-bot 里的表格文件
搜索文件名包含 batch 的文件
列出 Z 盘有哪些文件
复制 Z:\公共机共享\抠图\input\a.png 到 E:\assetclaw-matting-bot\storage\debug\a.png
把 Z:\公共机共享\抠图\input\a.png 通过飞书发给我
把共享盘 input 文件夹压缩成 zip 并发送给我
把这句话翻译成英文：今天辛苦了
把刚刚发的图片里的文字翻译成中文
提取刚刚那张图里的文字
查看 E:\assetclaw-matting-bot\README.md 的信息
查看 D E F 盘空间
把 D:\assets 复制到 F:\backup\assets
把这几个文件批量复制到 F:\backup
把刚才列出的图片按顺序改名为 1 2 3 4 5
把 E:\a.png 转成 jpg
把 E:\a.png 缩放成 1024x1024
把 F:\assets.zip 解压到 F:\assets
看看这个 zip 里面有哪些文件
统计 E:\input 里有多少图片
在 E:\assetclaw-matting-bot 里搜索 ComfyUI
把 E:\input 的文件清单导出成 E:\input_manifest.csv
查看这个 CSV 有哪些列
计算 F:\package.zip 的 sha256
读取 D:\project\README.md 前 2000 个字符
把 E:\a.png 复制一份并改名为 a_bak.png
把 E:\a.png 在原路径复制一份，后缀加 _bak
把 E:\assetclaw-matting-bot\README.md 通过飞书发给我
把 E 盘里 img_v3_02125_53d2b164...608g.png 发给我
用 E:\assetclaw-matting-bot\storage\batch_inputs 创建一个抠图批次
选择 软边缘测试-动画批量.json 作为抠图工作流
预览 E:\input 到 E:\output 的抠图任务
开始批量抠图
现在 ComfyUI 跑到多少张了
我们现在有哪些任务
把这个任务的输入路径改成 E:\input2
暂停当前抠图任务
继续当前抠图任务
终止当前抠图任务
删除这个失败的 ComfyUI 任务记录
同步这个 ComfyUI 任务的输出结果 COMFY_XXXXXXXXXXXX
查看 Cherry 帧序列工具状态
预览 E:\output 到 E:\smooth_output 的 Cherry 平滑任务
对 E:\output 做 Cherry 平滑处理，输出到 E:\smooth_output
现在 Cherry 任务跑到哪里了
我们现在有哪些平滑任务
终止当前 Cherry 任务
查看飞书抽帧工具状态
开始飞书抽帧，下载到 E:\raw_videos，抽帧输出 E:\output_frames
抽帧任务跑到哪里了
执行动画自动化流程 E:\raw_videos E:\output_frames E:\output_matting E:\output_smooth
自动化流程现在跑到哪里了
共享盘抠图任务现在跑到哪里了
查看当前系统状态
现在显卡使用情况怎么样
ComfyUI 状态
```

默认本地批量抠图使用 `E:\input` 到 `E:\output`，递归处理图片，并保留输入目录结构。启动前会返回任务摘要并要求确认；运行中采用安静进度推送，状态变化/明显进度变化/完成/失败会主动通知，用户主动问进度会立即返回。

Cherry 帧序列处理工具位于 `E:\assetclaw-matting-bot\Cherry_帧序列处理工具`。Agent 调用时会递归读取输入目录图片，按父文件夹分组做时序 Alpha 平滑、缩放、锐化，输出到目标目录并保留相同相对结构。实际处理使用秋叶环境 `C:\Users\lilithgames\Downloads\ComfyUI-aki-v3\python\python.exe`，不要求 bot 的 conda 环境安装 torch。启动前会给图片数、序列数、参数摘要并要求确认。

飞书抽帧工具位于 `E:\assetclaw-matting-bot\feishu_frame_tool`。完整动画自动化流程固定为三步：`frame.run_start` 抽帧输出到 `E:\output_frames`，再由 `comfyui.run_start` 抠图输出到 `E:\output_matting`，最后由 `cherry.run_start` 平滑输出到 `E:\output_smooth`。总流程会保存每个子任务 run_id，方便单独查错和返工。

## 暂不实现

任意 shell、格式化磁盘、分区、改盘符、访问 C 盘、访问密钥文件、内网穿透暂不接入。删除和清空目录已接入，但必须二次确认。真实 GPU / P4 后续接入时默认走二次确认。
