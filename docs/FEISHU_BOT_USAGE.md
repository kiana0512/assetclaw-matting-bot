# 飞书机器人使用说明

AssetClaw Win3090 机器人通过飞书长连接接收指令，在本机执行安全范围内的自动化任务，并把结果回复到当前飞书会话。

无需公网 IP、无需 Cloudflare、无需内网穿透。

## 回复风格

- 普通对话尽量短，不输出大段 AI 式说明。
- 能直接执行的任务会直接执行。
- 发文件、列目录、复制文件、创建目录这类低风险动作不需要二次确认。
- 删除、移动、批量重命名、清空目录、解压覆盖等高风险动作会先发确认码。
- 上下文太长时会自动整理旧对话，并在飞书提示“已自动整理较早对话”。

## 权限边界

允许访问和操作：

- `D:\`
- `E:\`
- `F:\`

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
列出 F:\projects 下的文件
列出 E 盘全部图片
查找 D 盘名字里包含 storyboard 的文件
```

### 发送文件到飞书

```text
把 E:\a.png 发给我
把刚刚那个图片发给我
把 img_v3_xxx.png 通过飞书发给我
```

发送文件不需要二次确认。

### 复制 / 新建目录

```text
在 E 盘新建 images 文件夹
把 E:\a.png 复制到 F:\images\a.png
把 E:\images 连同里面的文件复制到 F 盘
把刚刚提到的图片复制到新建的 images 文件夹
```

### 重命名 / 批量重命名

```text
把 E:\a.png 改名为 E:\1.png
把这些图片按照排列顺序改成 1.png 2.png 3.png
把 E:\images 里的图片按顺序重命名为 001、002、003
```

重命名属于高风险动作，会二次确认。

### 删除 / 移动 / 清空

```text
删除 E:\temp\a.png
把 E:\images 移动到 F:\images
清空 E:\temp
```

这些动作会二次确认。机器人不会执行格式化、分区、系统目录删除。

### 文本文件

```text
读取 E:\notes\todo.txt
把“完成测试”写入 E:\notes\status.txt
在 E:\notes\log.txt 末尾追加“已处理”
```

### 图片处理

```text
查看 E:\a.png 的图片信息
查看这些图片的尺寸
把 E:\a.png 转成 jpg
把 E:\a.png 缩放到 1024 宽
```

### 压缩 / 解压

```text
把 E:\images 打包成 E:\images.zip
把 E:\images.zip 解压到 F:\images
```

打包和解压会按风险策略确认，解压会限制在 D/E/F 内。

### 状态查询

```text
现在显卡使用情况怎么样
查看 nvidia-smi 结果
查看 comfyui 状态
查看磁盘空间
查看 python 进程
```

GPU 查询会返回显存、利用率、温度、功耗。ComfyUI 查询会返回 fake/real mode、URL、工作流路径、连接状态。

## 抠图批次

当前批次能力仍以安全封装为主，fake mode 下不会真正跑 GPU。

```text
用 E:\batch_inputs 创建一个抠图批次
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

**机器人只回复“完成”但没结果**  
这是不合格输出。状态类技能应该返回具体信息，例如 GPU 显存、ComfyUI 模式和连接状态。

**机器人说“我理解了”但没执行**  
这是需要修复的问题。现在会尽量拦截这种空回复，改成说明“没有执行任何操作”并提示缺少的路径或文件名。

**路径被拒绝**  
确认路径在 `D:\`、`E:\`、`F:\` 下，并且没有命中敏感目录规则。

**机器人无回复**  
检查本地启动脚本和 `logs` 目录，确认飞书 WebSocket receiver 正在运行。
