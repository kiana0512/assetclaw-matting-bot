# Matting / ComfyUI Workflow

目标流程：用户选择 `workflow_path`，指定 `input_dir`、`output_dir`，机器人把输入目录中的图片逐张提交给 ComfyUI，并能从飞书查询当前管线、进度、ETA、输入输出目录和 GPU 状态。

当前 ComfyUI 由秋叶启动器管理：

```text
Aki 根目录：C:\Users\lilithgames\Downloads\ComfyUI-aki-v3
ComfyUI：C:\Users\lilithgames\Downloads\ComfyUI-aki-v3\ComfyUI
Python：C:\Users\lilithgames\Downloads\ComfyUI-aki-v3\python
后端：http://127.0.0.1:8188
```

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
选择 C:\Users\lilithgames\Downloads\ComfyUI-aki-v3\ComfyUI\user\default\workflows\软边缘测试-动画批量.json 作为抠图工作流
预览 E:\input 到 E:\output 的抠图任务
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

- 输入目录：`E:\input`
- 输出目录：`E:\output`
- 递归处理输入目录下所有图片。
- 输出目录保留输入目录结构。
- 默认使用安静进度推送，避免刷屏；完成和失败会立即通知。
- 完成或报错时主动推送飞书消息。
- 启动前会先返回 workflow、输入输出目录、图片总数和关键节点，并要求确认。
- 暂停会等当前图片结束后停止提交后续图片；终止会停止后续图片，并尝试中断当前 ComfyUI 队列。

例如：

```text
E:\input\Jessica-happy\a.png
E:\input\Jessica-idle\b.jpg
```

会输出到：

```text
E:\output\Jessica-happy\a.png
E:\output\Jessica-idle\b.png
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
