# 飞书机器人使用说明

AssetClaw Win3090 机器人通过飞书长连接接收指令，在本机执行安全范围内的自动化任务，并把结果回复到当前飞书会话。

无需公网 IP、无需 Cloudflare、无需内网穿透。

## 回复风格

- 普通对话尽量短，不输出大段 AI 式说明。
- 能直接执行的任务会直接执行。
- 发文件、列目录、复制文件、创建目录这类低风险动作不需要二次确认。
- 删除、移动、批量重命名、清空目录、解压覆盖等高风险动作会先发确认码。
- 上下文太长时会自动整理旧对话，并在飞书提示“上下文已整理，会继续接着聊。”。
- 进度类提问会尽量给原消息加飞书表情反应；需要文字时只回一条精简状态。

## 权限边界

允许访问和操作：

- `D:\`
- `<allowed-root>\`
- `<secondary-root>\`
- `Z:\`
- `\\audioshare.lilith.com\AIart\公共机共享\抠图`

禁止访问或操作：

- `C:\`
- `.env`、`.ssh`、`Windows`、`Program Files`、`ProgramData`
- `$Recycle.Bin`、`System Volume Information`
- 任意 Shell 命令、格式化磁盘、分区、系统级危险操作

## 常用指令

### 查看能力

```text
你可以做什么
查看技能列表
查看权限说明
```

### 列目录 / 找文件

```text
列出 E 盘的文件
列出 <secondary-root>\projects 下的文件
列出 E 盘全部图片
查找 D 盘名字里包含 storyboard 的文件
```

### 发送文件到飞书

```text
把 <allowed-root>\a.png 发给我
把刚刚那个图片发给我
把 img_v3_xxx.png 通过飞书发给我
把 <allowed-root>\1.png 用图片形式发给我
```

发送文件不需要二次确认。

如果希望图片直接显示在聊天里，而不是文件卡片，说“用图片形式”“预览”“展示”。

## 多模态消息

机器人可以接收飞书里的图片、视频和文件。

收到图片/视频后会先保存到：

```text
<project-root>\storage\feishu_inbox\日期\会话\
```

然后你可以继续说：

```text
保存到 <allowed-root>\images
查看这张图的尺寸
预览发回给我
把刚刚那张图复制到 <secondary-root>\review
分析这张图里有什么
```

当前支持：

- 图片：接收、保存、查看尺寸、复制、改名、转格式、缩放、飞书图片形式发回、交给支持视觉的 LLM 分析
- 视频：接收、保存、查看文件信息、复制、移动、飞书文件形式发回
- 普通文件：接收、保存、查看信息、复制、发回

视觉分析依赖 `LLM_PROXY` 是否支持多模态。如果代理不支持，机器人会明确提示不能分析，而不是只回复“完成”。

### 复制 / 新建目录

```text
在 E 盘新建 images 文件夹
把 <allowed-root>\a.png 复制到 <secondary-root>\images\a.png
把 <allowed-root>\images 连同里面的文件复制到 F 盘
把刚刚提到的图片复制到新建的 images 文件夹
```

### 重命名 / 批量重命名

```text
把 <allowed-root>\a.png 改名为 <allowed-root>\1.png
把这些图片按照排列顺序改成 1.png 2.png 3.png
把 <allowed-root>\images 里的图片按顺序重命名为 001、002、003
```

重命名属于高风险动作，会二次确认。

### 删除 / 移动 / 清空

```text
删除 <allowed-root>\temp\a.png
把 <allowed-root>\images 移动到 <secondary-root>\images
清空 <allowed-root>\temp
```

这些动作会二次确认。机器人不会执行格式化、分区、系统目录删除。

### 文本文件

```text
读取 <allowed-root>\notes\todo.txt
把“完成测试”写入 <allowed-root>\notes\status.txt
在 <allowed-root>\notes\log.txt 末尾追加“已处理”
```

### 图片处理

```text
查看 <allowed-root>\a.png 的图片信息
查看这些图片的尺寸
把 <allowed-root>\a.png 转成 jpg
把 <allowed-root>\a.png 缩放到 1024 宽
```

### 翻译

```text
把这句话翻译成英文：今天辛苦了
翻译成日语：这个文件已经处理好了
把刚刚发的图片里的文字翻译成中文
识别这张图里的英文并翻译成中文
```

文本翻译会直接返回自然语言译文，不加长解释。图片翻译会依赖支持视觉的 LLM Proxy：先识别图中文字，再按目标语言翻译。

如果只想提取图片文字，不翻译，可以说：

```text
提取刚刚那张图里的文字
识别 <allowed-root>\a.png 里的文字
```

图片 OCR 当前支持图片，不支持视频文件。视频可以保存、查看信息、发送，但不会交给 Sonnet 做视频理解。

## 直传动画图片处理

发一张或多张图片后，机器人会直接处理，不需要确认。

流程：

```text
图片 -> ComfyUI 抠图 -> Cherry HTML 后处理 -> 文件附件回传
```

回传结果会按文件附件发送，不按普通图片发送，避免飞书二次压缩。

后处理会按原图比例自动选择预设：

- 宽高完全相同：正方形 `256x256`
- 宽高不同：长方形 `384x512`

状态回复会带当前使用的后处理预设，例如：

```text
⌨️ IMG_xxx：postprocess RUNNING，1 张，抠图 1/1，后处理 长方形 384x512x1
```

## 直传动画视频处理

如果要让机器人处理动画视频，必须把 `.mp4/.mov` 当作“文件”发送，不要作为飞书“视频”消息发送。

原因：飞书视频消息会进入 media 通道并可能被转码压缩，机器人拿到的不是原始码率版本。文件附件会按文件资源下载，适合用于抽帧、抠图和后处理。

推荐发法：

```text
点击输入框旁边的 + / 文件 / 本地文件，选择 source.mp4
```

不要直接拖成带播放按钮的视频卡片。机器人收到视频卡片时会提示改用文件发送，不会启动处理。

文件形式的视频触发后，机器人会先要求确认：

```text
收到 1 个动画视频，是否开始处理？
确认执行：确认执行 xxxxxxxxxx
```

确认后流程是：

```text
原视频 -> OpenCV 抽帧 -> ComfyUI 抠图 -> Cherry 后处理 -> zip 回传
```

后处理会按视频原始宽高自动选择预设：

- 宽高完全相同：正方形 `256x256`
- 宽高不同：长方形 `384x512`

主动通知策略比较安静：只在任务开始、大阶段开始/完成、失败、最终 zip 回传时主动发消息。中间细粒度进度不会刷屏；需要时可以随时问：

```text
这个视频处理进度到哪了
动画处理进度
进度如何了
```

机器人会自动定位最近的直传图片/视频任务，并返回一条精简状态。

如果失败，回复里会带子任务阶段和最近错误。ComfyUI 失败通常表示某一帧在工作流执行、输出校验或输出同步时出错；抽帧数量和原视频路径会保留在任务状态里，方便复查。

## 语音消息 / ASR / TTS

机器人可以接收飞书语音。收到语音后会先提示：

```text
收到语音了。我会先用本地 ASR 转文字，通常 2-8 秒，首次加载模型可能需要 20 秒以上。
```

ASR 识别出的文字会被当作新的用户指令继续处理，所以可以直接用语音说：

```text
查看 GPU 状态和当前任务
自动化流程现在跑到哪里了
把刚刚那张图里的文字翻译成中文
开启语音回复
```

语音回复开关：

```text
开启语音回复
关闭语音回复
只发文字
```

开启语音回复后，机器人会先发文字结果，再补发 TTS 语音。这样即使本地 TTS 模型加载较慢，用户也不会一直空等。

如果飞书语音附件没有成功下载到本地，机器人会提示“语音还没下载到本地”，不会误判成图片 OCR。

## 唱歌和接歌词

唱歌模式、接歌词、续下一句功能已经关闭。飞书里说“进入唱歌模式”“陪我唱歌”“不要再接下一句了”时，机器人只会提示该功能已关闭，并继续按正常聊天或工具指令处理。

### 压缩 / 解压

```text
把 <allowed-root>\images 打包成 <allowed-root>\images.zip
把共享盘 input 文件夹压缩成 zip 并发送给我
把 <allowed-root>\images.zip 解压到 <secondary-root>\images
```

单纯打包、打包后发送不需要二次确认；解压会按风险策略确认，并限制在允许路径内。

### 状态查询

```text
现在显卡使用情况怎么样
查看 nvidia-smi 结果
查看 comfyui 状态
ComfyUI 现在在跑什么管线
ComfyUI 跑到多少张了
查看磁盘空间
查看 python 进程
```

GPU 查询会返回显存、利用率、温度、功耗。ComfyUI 查询会返回 fake/real mode、URL、工作流路径、连接状态。

### ComfyUI 管线

```text
现在用的抠图管线是什么
当前抠图管线版本
验证抠图管线
更新抠图管线
列出当前有哪些 ComfyUI 工作流
选择 软边缘测试-动画批量.json 作为抠图工作流
查看当前工作流的节点信息
预览 <allowed-root>\input 到 <allowed-root>\output 的抠图任务
开始批量抠图
现在 ComfyUI 跑到多少张了
暂停当前抠图任务
继续当前抠图任务
终止当前抠图任务
同步这个 ComfyUI 任务的输出结果 COMFY_XXXXXXXXXXXX
```

GitLab 管理的默认抠图管线是 `ImageClip`：

- 远程仓库：`git@gitlab.lilithgame.com:rd_center/ai_art/imageclip.git`
- 本地仓库：`<pipeline-root>`
- 默认工作流：`<comfyui-root>\ComfyUI\user\default\workflows\ImageClip.json`
- 同步内容：`ImageClip.json`、`Koutu_Flux2klein_v2_000007250.safetensors`、`Cherry_lizi`、`cherry-postprocess.html`

`更新抠图管线` 会拉取 GitLab 最新版本，并把 workflow、lora、custom node 同步到秋叶 ComfyUI。同步优先使用软链接；如果 Windows 权限不允许创建软链接，会自动复制并在回复里显示状态。机器人不会在 ComfyUI 有任务运行时强制重启；需要刷新 custom node 或 lora 时，应等队列空闲后通过秋叶启动器重启或重新加载。

直传图片/视频的 Cherry 后处理算法只以 `<pipeline-root>\cherry-postprocess.html` 为准。机器人必须用无头浏览器调用这份 HTML；HTML 缺失、结构不兼容或浏览器执行失败时任务会明确失败，不会切换到 Python 近似算法。正方形 256×256 与长方形 384×512 使用 HTML 中各自的预设路线。

ComfyUI 管线会记录：

- workflow 路径
- 输入目录
- 输出目录
- 总图片数、已完成数、失败数
- ComfyUI 原生队列状态
- 预计剩余时间
- GPU 显存、利用率、温度、功耗

真实模式下启动管线会调用 ComfyUI 后端：

- `/system_stats`
- `/upload/image`
- `/prompt`
- `/queue`
- `/history/{prompt_id}`
- `/view`

输出同步会从 ComfyUI 输出记录里下载结果到你指定的 `output_dir`。
启动本地批量抠图前会先返回 workflow、输入输出目录、图片总数和关键节点，并要求确认。

### 共享盘抠图

共享盘路径已纳入允许范围：

```text
Z:\公共机共享\抠图
\\audioshare.lilith.com\Alart\公共机共享\抠图
\\audioshare.lilith.com\AIart\公共机共享\抠图
```

`Z:\` 是 `\\audioshare.lilith.com\AIart` 的本机映射。可以直接让机器人列目录、复制、发送共享盘文件。

共享盘抠图不会直接在共享盘上跑。流程是：

```text
共享盘输入目录 -> 本地 storage\matting_runs\SMAT_xxx\input
本地 ComfyUI 输出 -> 本地 storage\matting_runs\SMAT_xxx\output
本地输出 -> 同步回共享盘输出目录
```

常用说法：

```text
列出 Z 盘有哪些文件
把 Z:\公共机共享\抠图\input\a.png 复制到 <project-root>\storage\debug\a.png
把 Z:\公共机共享\抠图\input\a.png 通过飞书发给我
把 Z:\公共机共享\抠图\input052901 用 <project-root>\workflows\matting_api.json 跑抠图，输出到 Z:\公共机共享\抠图\output052901
共享盘抠图任务现在跑到哪里了
把共享盘抠图任务 SMAT_XXXXXXXXXXXX 的结果同步回去
```

启动后机器人会使用安静进度推送，避免刷屏；任务完成会主动提示，中途失败也会主动提示错误。你也可以随时问“现在 ComfyUI 跑到哪里了”来立即查看。

## 抠图批次

当前批次能力仍以安全封装为主，fake mode 下不会真正跑 GPU。

```text
用 <allowed-root>\batch_inputs 创建一个抠图批次
查看最近批次
查看批次 BATCH_xxx 的状态
启动批次 BATCH_xxx
暂停批次 BATCH_xxx
恢复批次 BATCH_xxx
取消批次 BATCH_xxx
```

## 确认码

高风险动作会返回类似：

```text
需要确认：file.rename_sequence
回复：确认执行 835549a370
```

复制确认码回复后才会执行。

## 常见问题

**机器人过很久突然回复旧消息**  
A: 已加旧事件过滤。飞书重放超过 `FEISHU_IGNORE_EVENTS_OLDER_THAN_SECONDS` 的旧消息时，只记录为 ignored，不再回复。

**机器人只回复“完成”但没结果**  
这是不合格输出。状态类技能应该返回具体信息，例如 GPU 显存、ComfyUI 模式和连接状态。

**机器人说“我理解了”但没执行**  
这是需要修复的问题。现在会尽量拦截这种空回复，改成说明“没有执行任何操作”并提示缺少的路径或文件名。

**路径被拒绝**  
确认路径位于 `ALLOWED_ROOTS` 配置的授权根目录下，并且没有命中敏感目录规则。

**机器人无回复**  
检查本地启动脚本和 `logs` 目录，确认飞书 WebSocket receiver 正在运行。
