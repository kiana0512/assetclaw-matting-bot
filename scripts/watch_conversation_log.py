from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def tail_lines(path: Path, line_count: int) -> tuple[list[str], int]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return [], 0

    size = path.stat().st_size
    block_size = 8192
    data = bytearray()
    with path.open("rb") as handle:
        position = size
        while position > 0 and data.count(b"\n") <= line_count:
            read_size = min(block_size, position)
            position -= read_size
            handle.seek(position)
            data[:0] = handle.read(read_size)

    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return lines[-line_count:], size


def read_new(path: Path, position: int) -> tuple[str, int]:
    if not path.exists():
        return "", 0
    size = path.stat().st_size
    if size < position:
        position = 0
    if size == position:
        return "", position
    with path.open("rb") as handle:
        handle.seek(position)
        data = handle.read()
    return data.decode("utf-8", errors="replace"), size


def key_pressed_quit() -> bool:
    if os.name != "nt":
        return False
    try:
        import msvcrt

        if not msvcrt.kbhit():
            return False
        key = msvcrt.getwch()
        return key in {"q", "Q", "\x03"}
    except Exception:
        return False


def format_log_line(line: str) -> str:
    raw = line.strip()
    if not raw:
        return ""
    try:
        item = json.loads(raw)
    except Exception:
        return raw

    ts = str(item.get("ts") or "")
    clock = ts[11:19] if len(ts) >= 19 else ts
    event = str(item.get("event") or "")
    text = str(item.get("text") or "")

    if event == "feishu.incoming":
        return f"[{clock}] 用户: {text}"
    if event == "brain.input":
        provider = item.get("provider") or "brain"
        return f"[{clock}] 脑输入({provider}): {text}"
    if event == "brain.output":
        provider = item.get("provider") or "brain"
        return f"[{clock}] 脑输出({provider}): {text}"
    if event == "feishu.reply":
        return f"[{clock}] 回复: {text}"
    if event == "skill.call":
        provider = item.get("provider") or ""
        skill = item.get("skill") or ""
        suffix = f" via {provider}" if provider else ""
        return f"[{clock}] 技能调用: {skill}{suffix}"
    if event == "skill.result":
        skill = item.get("skill") or ""
        ok = item.get("ok")
        return f"[{clock}] 技能结果: {skill} ok={ok}"
    if event == "feishu.attachment_download_failed":
        return f"[{clock}] 附件下载失败: {item.get('resource_type')} {item.get('error')}"
    return f"[{clock}] {event}: {text or raw}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--tail", type=int, default=80)
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()

    print("---- conversation.log tail ----", flush=True)
    lines, position = tail_lines(args.path, args.tail)
    for line in lines:
        formatted = format_log_line(line)
        if formatted:
            print(formatted, flush=True)
    print("---- watching; press q or Ctrl+C to leave monitor ----", flush=True)

    pending = ""
    try:
        while True:
            chunk, position = read_new(args.path, position)
            if chunk:
                pending += chunk
                while "\n" in pending:
                    line, pending = pending.split("\n", 1)
                    formatted = format_log_line(line)
                    if formatted:
                        print(formatted, flush=True)
            if key_pressed_quit():
                print("\nLog monitor closed. Bot services are still running.", flush=True)
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nLog monitor closed. Bot services are still running.", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
