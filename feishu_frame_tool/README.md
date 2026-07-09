# 飞书 → 本地抽帧自动化工具

读取飞书多维表格的「进度」状态，自动下载「动画」视频，**在本地用 OpenCV 按指定帧率抽帧**导出 PNG 序列帧，可选去除相似帧，并把进度状态回写到表格。

> 不再依赖 framepacker.cn 网页：抽帧在本地完成，更快、更稳、可离线。

## 流程

| 表格「进度」状态 | 工具动作 |
| --- | --- |
| 有「动画」视频附件 | ①按角色/情绪下载视频 → ②本地抽帧导出 PNG 序列帧 → ③可选去除相似帧 |
| 无「动画」视频附件 | 跳过 |

- 抽帧帧率默认 **24**（目标帧率小于源帧率时重采样抽稀，否则导出每一帧）。
- 下载/导出目录均按「角色」层级路径命名，如 `表情动画_susan_idle`。
- 每次处理前会清空该记录对应的旧文件，保证结果是全新的。

## 安装

```bash
pip install -r requirements.txt
```

依赖：`requests`（飞书 API）、`opencv-python`（抽帧）、`Pillow` + `numpy`（相似帧去重）。

## 配置

复制示例配置并填写飞书凭证：

```bash
cp config.example.json config.json
```

`config.json` 关键字段：

- `feishu.app_id` / `feishu.app_secret`：飞书自建应用凭证。
- `feishu.table_url`：多维表格的浏览器链接（填了会自动解析出 app_token/table_id/view_id；留空则用下面已存的值）。
- `paths.download_dir` / `paths.export_dir`：下载与导出目录。
- `framepacker.fps`：抽帧帧率（默认 24）。导出按视频原始尺寸。
- `dedup.enabled` / `diff_threshold` / `renumber`：是否去除相似帧、相似阈值（越大删越多）、去重后是否重新连续编号。

### 飞书应用需要的权限

在 [飞书开放平台](https://open.feishu.cn/) 创建「自建应用」，开通以下权限并**发布版本**：

- `bitable:app`（查看、评论、编辑和管理多维表格）— 读写记录
- `drive:drive` 或 `docs:document.media:download` — 下载附件

并把该应用添加为目标多维表格的**协作者**（表格右上角「…」→「更多」→「添加文档应用」，或「分享」给应用），否则接口无法访问该表格。

## 运行

图形界面（推荐，启动前可改设置）：

```bash
python gui.py
```

也可以在启动时从外部覆盖界面默认值：

```bash
python gui.py --fps 24 --diff-threshold 0.2
```

日常直接**双击 `启动工具.bat`** 即可；可改其顶部的 `FPS` 和 `DIFF_THRESHOLD` 两个变量。也支持环境变量 `FEISHU_FRAME_FPS`、`FEISHU_FRAME_DIFF_THRESHOLD`（命令行参数优先级更高）。

命令行（服务器/无界面）：

```bash
python run.py
```

## 文件说明

- `feishu_client.py`：飞书多维表格读写 + 附件下载 + 链接解析。
- `extractor.py`：本地 OpenCV 抽帧，导出 PNG 序列帧。
- `dedup.py`：相似帧去重（Pillow + numpy）。
- `workflow.py`：编排逻辑、角色/情绪路径解析与 manifest 生成。
- `gui.py` / `run.py`：图形界面 / 命令行入口。

## 备注

- 抽帧输出为视频原始画面的 PNG（不做抠图/透明背景），按视频原始尺寸导出。
- 相似帧去重逐帧与「上一张保留帧」比对，差异低于阈值即删除；可在 GUI 调阈值。
