# Skill 开发指南

如何新增一个 Skill，让 AI Brain 和 Skill API 都能调用它。

---

## 一个 Skill 是什么

Skill 是一个受控的、可审计的 Python 函数，对外暴露为：
- `POST /skills/v1/call {"skill": "xxx.yyy", "arguments": {...}}`
- Brain Router 的 `tool_calls`
- MCP 工具（自动同步，无需额外代码）

所有 Skill 调用都会写入 `skill_calls` 审计表。

---

## 新增 Skill 的完整步骤

### 第一步：实现函数

在 `src/assetclaw_matting/skills/` 找到合适的文件，或新建一个：

```python
# src/assetclaw_matting/skills/your_module.py

from __future__ import annotations
from typing import Any


def your_skill_name(
    required_param: str,
    optional_param: int = 10,
) -> dict[str, Any]:
    """简短描述这个 skill 做什么。"""
    # 如果需要路径验证：
    from assetclaw_matting.skills.auth import validate_skill_path
    path = validate_skill_path(required_param)  # 自动检查 ALLOWED_ROOTS 和 DENY_PATH_PATTERNS
    
    # 业务逻辑
    result = do_something(path, optional_param)
    
    # 返回 dict，key 名字自由定义
    return {
        "param": str(path),
        "count": result,
    }
```

**规则：**
- 函数参数直接对应 Skill 的 `arguments` 字段（会做 `fn(**arguments)` 调用）
- 返回 `dict`，不要抛异常（用 `raise ValueError("描述")` 表示用户输入错误）
- 路径参数必须用 `validate_skill_path()` 验证
- 不允许执行 shell：`ALLOW_SHELL_EXEC=false`
- 不允许删除文件：`ALLOW_FILE_DELETE=false`

### 第二步：在 registry.py 里注册

打开 `src/assetclaw_matting/skills/registry.py`，在 `SKILL_CATALOG` 列表里加一条：

```python
# registry.py 顶部 import 里加上你的模块
from assetclaw_matting.skills import your_module

# SKILL_CATALOG 列表里加一条（用 _f 辅助函数）
_f(
    "your.skill_name",            # skill 名称（用点分隔）
    "描述这个 skill 做什么",       # description（AI Brain 会看这个）
    "low",                        # danger_level: low / medium / high
    False,                        # requires_confirmation: 中高风险操作改成 True
    True,                         # implemented: True 表示已实现
    your_module.your_skill_name,  # 函数引用
),
```

`_f` 函数签名：
```python
def _f(name, description, danger_level, requires_confirmation, implemented, fn) -> dict
```

危险级别说明：
- `low` — 只读操作，直接执行
- `medium` — 会修改数据（创建批次、取消等），建议 AI Brain 确认后执行
- `high` — 不可逆操作（删除文件、P4 提交等），**必须** `requires_confirmation=True`

### 第三步：验证

重启 Gateway 后检查：

```bash
# 看新 skill 是否出现在清单里
curl -H "X-Skill-Token: your_token" http://127.0.0.1:7865/skills/v1/manifest \
  | python -m json.tool | grep "your.skill"

# 直接调用测试
curl -X POST http://127.0.0.1:7865/skills/v1/call \
  -H "X-Skill-Token: your_token" \
  -H "Content-Type: application/json" \
  -d '{"skill":"your.skill_name","arguments":{"required_param":"E:\\test"}}'
```

### 第四步：更新 skills_pack 文档

在 `skills_pack/assetclaw_win3090/SKILL.md` 里加上新技能的说明和示例，让 AI Brain 知道怎么用它。

---

## 完整示例：新增 `image.resize` Skill

**需求：** 批量缩放某个目录的图片，输出到另一个目录。

### 实现函数（新建 `skills/image_skills.py`）

```python
# src/assetclaw_matting/skills/image_skills.py

from __future__ import annotations
from pathlib import Path
from typing import Any


def image_resize(
    input_dir: str,
    output_dir: str,
    max_width: int = 1920,
    max_height: int = 1080,
) -> dict[str, Any]:
    """Resize images in a directory to fit within max dimensions."""
    from assetclaw_matting.skills.auth import validate_skill_path
    from PIL import Image

    src = validate_skill_path(input_dir)
    dst = validate_skill_path(output_dir)
    dst.mkdir(parents=True, exist_ok=True)

    exts = {".png", ".jpg", ".jpeg", ".webp"}
    files = [f for f in src.iterdir() if f.suffix.lower() in exts]

    processed = 0
    for f in files:
        with Image.open(f) as img:
            img.thumbnail((max_width, max_height), Image.LANCZOS)
            img.save(dst / f.name)
            processed += 1

    return {
        "input_dir": str(src),
        "output_dir": str(dst),
        "processed": processed,
    }
```

### 注册到 registry.py

```python
# 顶部 import 加上
from assetclaw_matting.skills import image_skills

# SKILL_CATALOG 列表里加
_f("image.resize",
   "Resize images in a directory to fit within given dimensions",
   "medium", False, True, image_skills.image_resize),
```

### 调用

```bash
curl -X POST http://127.0.0.1:7865/skills/v1/call \
  -H "X-Skill-Token: your_token" \
  -H "Content-Type: application/json" \
  -d '{
    "skill": "image.resize",
    "arguments": {
      "input_dir": "E:\\source_images",
      "output_dir": "E:\\resized_images",
      "max_width": 1280,
      "max_height": 720
    }
  }'
```

---

## 实现已规划的 Future Skill

`SKILL_CATALOG` 里已经有 16 个 `implemented: False` 的 future skill，比如 `video.extract_frames`。

实现步骤：
1. 在 `skills/future_skills.py` 找到对应函数（目前返回 `_nyi(...)`）
2. 改成真实实现，或新建一个专门的模块
3. 把 `registry.py` 里对应条目的 `implemented` 改成 `True`，`fn` 改成新函数引用

例如激活 `video.extract_frames`：

```python
# registry.py 里找到这条，把 False 改 True，fn 指向新实现
_f("video.extract_frames", "Extract image sequence from video",
   "medium", False, True,   # ← implemented 改 True
   video_skills.extract_frames),  # ← fn 指向真实实现
```

---

## 路径安全辅助函数

所有涉及文件路径的 skill 必须用：

```python
from assetclaw_matting.skills.auth import validate_skill_path

# 会检查：
# 1. path 是否在 ALLOWED_ROOTS 内
# 2. path 是否包含 DENY_PATH_PATTERNS 中的字符串
# 3. 没有路径穿越（../）
# 返回 resolved Path，失败则 raise ValueError
path = validate_skill_path(user_input_path)
```

检查文件名（日志类）：
```python
from assetclaw_matting.skills.auth import validate_log_name
name = validate_log_name("worker")  # 只允许 gateway/worker/app
```

脱敏日志行：
```python
from assetclaw_matting.skills.security import sanitize_log_line, sanitize_log_lines
clean_lines = sanitize_log_lines(raw_lines)  # 自动去掉 token=xxx 等敏感值
```

---

## 文件命名约定

| 文件 | 内容 |
|------|------|
| `skills/batch_skills.py` | 批次管理相关 |
| `skills/worker_skills.py` | Worker 和任务相关 |
| `skills/queue_skills.py` | 队列统计 |
| `skills/comfyui_skills.py` | ComfyUI 状态 |
| `skills/file_skills.py` | 文件列表 |
| `skills/log_skills.py` | 日志查看 |
| `skills/future_skills.py` | 未实现的占位 |
| `skills/image_skills.py` | 图像处理（示例，待创建） |
| `skills/video_skills.py` | 视频处理（待创建） |
| `skills/animation_skills.py` | 动画相关（待创建） |

新模块放在 `skills/` 下，文件名 `{领域}_skills.py`，函数名 `{动作}_{名词}`。
