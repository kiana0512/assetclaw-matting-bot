# Matting / ComfyUI Workflow

> 本机 4070Ti 与 GPU Control 集群的混合抠图边界、请求/返回字段、任务隔离和联调验收，见
> [GPU_CONTROL_MATTING_HANDOFF_V2_IMPLEMENTATION.md](GPU_CONTROL_MATTING_HANDOFF_V2_IMPLEMENTATION.md)。远端只负责 ImageClip 抠图，其余步骤仍由动画管家执行。
> 调度前容量握手、双端持久化和故障恢复约定见 [GPU_CONTROL_SCHEDULER_HANDSHAKE_V2_1.md](GPU_CONTROL_SCHEDULER_HANDSHAKE_V2_1.md)。

目标流程：用户选择 `workflow_path`，指定 `input_dir`、`output_dir`，机器人把输入目录中的图片逐张提交给 ComfyUI，并能从飞书查询当前管线、进度、ETA、输入输出目录和 GPU 状态。

当前 ComfyUI 由秋叶启动器管理：

```text
Aki 根目录：<comfyui-root>
ComfyUI：<comfyui-root>\ComfyUI
Python：<comfyui-root>\python
后端：http://127.0.0.1:8188
```

## GitLab 管线同步

默认抠图管线由公司 GitLab 仓库管理：

```text
远程仓库：git@gitlab.lilithgame.com:rd_center/ai_art/imageclip.git
本地仓库：<pipeline-root>
默认工作流：<comfyui-root>\ComfyUI\user\default\workflows\ImageClip.json
```

机器人支持在飞书里直接询问或更新：

```text
现在用的抠图管线是什么
当前抠图管线版本
验证抠图管线
更新抠图管线
```

同步内容：

- `ImageClip.json` -> `ComfyUI\user\default\workflows\ImageClip.json`
- `Koutu_Flux2klein_v2_000007250.safetensors` -> `ComfyUI\models\loras\Koutu_Flux2klein_v2_000007250.safetensors`
- `Cherry_lizi` -> `ComfyUI\custom_nodes\Cherry_lizi`

同步优先创建软链接；如果当前 Windows 权限不允许创建软链接，会复制文件或目录作为兜底。更新后需要重启 ComfyUI 或用秋叶重新加载。机器人会在状态里返回 Git branch、commit、commit 时间、工作流路径、资源链接状态，并会检查 lora 是否疑似 Git LFS pointer。

公共 C 盘机器的默认布局中，`<pipeline-root>` 会自动解析为 `C:\imageclip`。它与项目目录同级，源码中不固定盘符；项目整体迁到其他盘时也会跟随项目父目录推导。

环境边界：

- 启动 ComfyUI：只使用秋叶目录里的 `python\python.exe`。
- Agent / Gateway / 飞书 / ASR / TTS / P4 / 文件技能：只使用 conda env `assetclaw`。
- `comfyui.run_start` 运行在 Agent 里，但它只向 `COMFYUI_URL` 发 HTTP 请求，不直接导入或安装 ComfyUI 依赖。
- `cherry.run_start` 通过 Chrome/Edge 直接执行 `C:\imageclip\cherry-postprocess.html`；不会调用机器人 Conda Python 或秋叶 Python 来替代该算法。

启动 ComfyUI 后端：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\run_comfyui.ps1
```

这个脚本不会激活 `assetclaw`，也不会修改秋叶环境；它只进入秋叶 ComfyUI 目录并调用秋叶 python。

## 当前可用

旧批次技能仍保留，用于轻量批次管理：

- `matting.batch_create`
- `matting.batch_start`
- `matting.batch_status`
- `matting.batch_pause`
- `matting.batch_resume`
- `matting.batch_cancel`

新的 ComfyUI 深度技能：

- `comfyui.workflows`：列出本地 workflow json
- `comfyui.workflow_info`：查看 workflow 节点、LoadImage、SaveImage
- `comfyui.workflow_select`：选择本次对话默认 workflow
- `comfyui.queue_status`：查询 ComfyUI 原生 `/queue`
- `comfyui.run_start`：指定 workflow/input/output 启动一批图片任务
- `comfyui.run_status`：查询当前/指定 run 的进度、ETA、目录、队列、GPU
- `comfyui.run_sync_outputs`：把完成的 ComfyUI 输出下载到指定输出目录

## 对齐的 ComfyUI API

真实模式下会调用：

- `GET /system_stats`
- `POST /upload/image`
- `POST /prompt`
- `GET /queue`
- `GET /history/{prompt_id}`
- `GET /view`

## 自然语言示例

```text
列出当前有哪些 ComfyUI 工作流
查看当前工作流的节点信息
选择 <comfyui-root>\ComfyUI\user\default\workflows\软边缘测试-动画批量.json 作为抠图工作流
预览 <allowed-root>\input 到 <allowed-root>\output 的抠图任务
开始批量抠图
ComfyUI 现在在跑什么管线
现在跑到多少张了，大概还需要多久
暂停当前抠图任务
继续当前抠图任务
终止当前抠图任务
同步这个 ComfyUI 任务的输出结果 COMFY_XXXXXXXXXXXX
```

## 注意

ComfyUI 的 `SaveImage` 默认写到 ComfyUI 自己的 output 区域。机器人会通过 history 里的输出记录，再用 `/view` 下载到用户指定的 `output_dir`。

如果 workflow 里有多个 `LoadImage` 节点，可以在 skill 参数里指定 `input_node_id` 和 `input_name`。不指定时默认 patch 第一个 `LoadImage.inputs.image`。

默认批量抠图约定：

- 输入目录：`<allowed-root>\input`
- 输出目录：`<allowed-root>\output`
- 递归处理输入目录下所有图片。
- 输出目录保留输入目录结构。
- 默认使用安静进度推送，避免刷屏；完成和失败会立即通知。
- 完成或报错时主动推送飞书消息。
- 启动前会先返回 workflow、输入输出目录、图片总数和关键节点，并要求确认。
- 暂停会等当前图片结束后停止提交后续图片；终止会停止后续图片，并尝试中断当前 ComfyUI 队列。

例如：

```text
<allowed-root>\input\Jessica-happy\a.png
<allowed-root>\input\Jessica-idle\b.jpg
```

会输出到：

```text
<allowed-root>\output\Jessica-happy\a.png
<allowed-root>\output\Jessica-idle\b.png
```

## 共享盘抠图

公共共享盘路径：

```text
Z:\公共机共享\抠图
\\audioshare.lilith.com\Alart\公共机共享\抠图
\\audioshare.lilith.com\AIart\公共机共享\抠图
```

其中 `Z:\` 是 `\\audioshare.lilith.com\AIart` 的本机映射。

共享盘抠图使用 staging 模式：

1. 从共享盘输入目录复制图片到本地 `storage\matting_runs\SMAT_xxx\input`。
2. 本地调用 ComfyUI workflow。
3. 输出先进入本地 `storage\matting_runs\SMAT_xxx\output`。
4. 完成后同步回共享盘输出目录。
5. 飞书默认安静推送进度，完成/失败都会主动通知；手动问进度会立即返回。

相关 skills：

- `matting.shared_start`
- `matting.shared_status`
- `matting.shared_sync_outputs`
