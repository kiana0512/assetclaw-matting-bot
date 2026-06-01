from __future__ import annotations

import re
from pathlib import Path

from assetclaw_matting.brain.schemas import BrainMessage, ToolCall

IMAGE_NAME_RE = re.compile(r"[\w.\-]+\.(?:png|jpg|jpeg|webp|bmp|gif|tif|tiff)", re.IGNORECASE)
DRIVE_RE = re.compile(r"([D-Fd-f])\s*(?:盘|:|：|的目录|目录)")


def plan_file_task(message: BrainMessage) -> tuple[list[ToolCall], str] | tuple[None, str] | None:
    text = message.text.strip()
    if not text:
        return None

    if _looks_like_copy_image_to_new_folder(text):
        image_name = _extract_image_name(text) or _last_image_name(message.conversation_id)
        folder_name = _extract_folder_name(text)
        if not image_name:
            return None, "可以。你想复制哪张图片？"
        if not folder_name:
            return None, f"可以，图片是 {image_name}。新文件夹叫什么名字？"
        return _copy_image_plan(image_name, folder_name)

    if _looks_like_folder_name_followup(text, message.conversation_id):
        image_name = _last_image_name(message.conversation_id)
        folder_name = _extract_folder_name(text)
        if image_name and folder_name:
            return _copy_image_plan(image_name, folder_name)

    if _looks_like_copy_recent_folder(text):
        src = _last_created_folder_path(message.conversation_id)
        dst_drive = _extract_destination_drive(text)
        if not src:
            return None, "可以。你要复制哪个文件夹？"
        if not dst_drive:
            return None, "可以。要复制到哪个盘或目录？"
        dst = f"{dst_drive}:\\{Path(src).name}"
        return (
            [
                ToolCall(
                    skill="file.copy_tree",
                    arguments={"src_path": src, "dst_path": dst, "overwrite": False},
                )
            ],
            f"我会把 {src} 连同里面的文件复制到 {dst}。",
        )

    if _looks_like_rename_recent_images_sequence(text):
        paths = _last_listed_image_paths(message.conversation_id)
        if not paths:
            return None, "可以。你要重命名哪些图片？先列一下目录或把文件名发给我。"
        start = _extract_sequence_start(text)
        return (
            [
                ToolCall(
                    skill="file.rename_sequence",
                    arguments={
                        "paths": paths,
                        "start": start,
                        "padding": 0,
                        "preserve_extension": True,
                        "overwrite": False,
                    },
                )
            ],
            f"我会把刚才列出的 {len(paths)} 张图片按顺序改名，从 {start} 开始。",
        )

    return None


def _copy_image_plan(image_name: str, folder_name: str) -> tuple[list[ToolCall], str]:
    safe_folder = Path(folder_name.strip()).name
    src = f"E:\\{image_name}"
    dst_dir = f"E:\\{safe_folder}"
    return (
        [
            ToolCall(skill="file.mkdir", arguments={"path": dst_dir}),
            ToolCall(
                skill="file.copy_as",
                arguments={
                    "src_path": src,
                    "new_name": image_name,
                    "dst_dir": dst_dir,
                    "overwrite": False,
                },
            ),
        ],
        f"我会创建 E:\\{safe_folder}，然后把 {image_name} 复制进去。",
    )


def _looks_like_copy_image_to_new_folder(text: str) -> bool:
    return (
        any(word in text for word in ("新建", "新增", "创建"))
        and "文件夹" in text
        and any(word in text for word in ("复制", "放进", "放到", "拷贝"))
        and any(word in text for word in ("图片", ".png", ".jpg", ".jpeg", ".webp"))
    )


def _looks_like_folder_name_followup(text: str, conversation_id: str) -> bool:
    if not conversation_id:
        return False
    if not _extract_folder_name(text):
        return False
    from assetclaw_matting.db.repos import get_recent_brain_messages

    recent = get_recent_brain_messages(conversation_id, limit=4)
    combined = "\n".join(item.get("message_text", "") + "\n" + item.get("response_text", "") for item in recent)
    return "新文件夹叫什么名字" in combined or "文件夹叫什么名字" in combined


def _looks_like_copy_recent_folder(text: str) -> bool:
    return (
        any(word in text for word in ("刚刚新增", "刚新增", "刚才新增", "刚刚创建", "刚才创建", "新增的文件夹", "创建的文件夹"))
        and "文件夹" in text
        and any(word in text for word in ("复制", "拷贝"))
        and (_extract_destination_drive(text) is not None or re.search(r"[D-Fd-f]:\\", text))
    )


def _looks_like_rename_recent_images_sequence(text: str) -> bool:
    return (
        any(word in text for word in ("这些图片", "刚才列出的图片", "刚刚列出的图片", "图片"))
        and any(word in text for word in ("改名", "修改名字", "重命名"))
        and any(word in text for word in ("顺序", "先后", "排列"))
        and re.search(r"1\s*2\s*3|123|1、2、3|1,2,3", text) is not None
    )


def _extract_image_name(text: str) -> str | None:
    match = IMAGE_NAME_RE.search(text)
    return match.group(0) if match else None


def _last_image_name(conversation_id: str) -> str | None:
    if not conversation_id:
        return None
    from assetclaw_matting.db.repos import get_recent_brain_messages

    recent = get_recent_brain_messages(conversation_id, limit=10)
    for item in reversed(recent):
        combined = f"{item.get('message_text', '')}\n{item.get('response_text', '')}"
        matches = IMAGE_NAME_RE.findall(combined)
        if matches:
            return matches[-1]
    return None


def _last_listed_image_paths(conversation_id: str) -> list[str]:
    if not conversation_id:
        return []
    from assetclaw_matting.db.repos import get_recent_brain_messages

    recent = get_recent_brain_messages(conversation_id, limit=10)
    for item in reversed(recent):
        response = item.get("response_text", "") or ""
        root_match = re.search(r"([D-Fd-f]:\\)\s*找到\s*\d+\s*项", response)
        if not root_match:
            continue
        root = root_match.group(1)[0].upper() + ":\\"
        names = IMAGE_NAME_RE.findall(response)
        if names:
            return [str(Path(root) / name) for name in names]
    return []


def _extract_sequence_start(text: str) -> int:
    match = re.search(r"(?:从|为|成)\s*(\d+)", text)
    if not match:
        return 1
    return max(0, int(match.group(1)))


def _last_created_folder_path(conversation_id: str) -> str | None:
    if not conversation_id:
        return None
    from assetclaw_matting.db.repos import get_recent_brain_messages

    recent = get_recent_brain_messages(conversation_id, limit=12)
    path_patterns = (
        r"已创建目录[:：]\s*([D-Fd-f]:\\[^\s。,\n，]+)",
        r"新建了文件夹\s*([A-Za-z0-9_.\-\u4e00-\u9fff]+)",
        r"新增了文件夹\s*([A-Za-z0-9_.\-\u4e00-\u9fff]+)",
        r"文件夹\s*([A-Za-z0-9_.\-\u4e00-\u9fff]+)",
    )
    for item in reversed(recent):
        combined = f"{item.get('message_text', '')}\n{item.get('response_text', '')}"
        for pattern in path_patterns:
            match = re.search(pattern, combined)
            if not match:
                continue
            value = match.group(1).strip().strip("。,.，")
            if re.match(r"^[D-Fd-f]:\\", value):
                return value[0].upper() + value[1:]
            if value.lower() not in {"这个", "刚刚", "刚才"}:
                return f"E:\\{Path(value).name}"
    return None


def _extract_destination_drive(text: str) -> str | None:
    match = DRIVE_RE.search(text)
    if not match:
        return None
    drive = match.group(1).upper()
    return drive if drive in {"D", "E", "F"} else None


def _extract_folder_name(text: str) -> str | None:
    patterns = (
        r"文件夹(?:就)?(?:叫|命名为|名字叫)\s*([A-Za-z0-9_.\-\u4e00-\u9fff]+)",
        r"(?:叫|命名为|名字叫)\s*([A-Za-z0-9_.\-\u4e00-\u9fff]+)",
        r"到\s*E:\\([A-Za-z0-9_.\-\u4e00-\u9fff]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            name = match.group(1).strip().strip("。,.，")
            if name.startswith("这个") and len(name) > 2:
                name = name[2:]
            if name:
                return name
    return None
