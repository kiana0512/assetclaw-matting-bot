from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 5:
        print(json.dumps({"ok": False, "error": "usage: script video_path output_dir fps max_frames"}), flush=True)
        return 2
    root_dir = Path(__file__).resolve().parents[1]
    tool_dir = root_dir / "feishu_frame_tool"
    sys.path.insert(0, str(root_dir))
    sys.path.insert(0, str(tool_dir))

    video = Path(sys.argv[1]).resolve()
    output_dir = Path(sys.argv[2]).resolve()
    fps = int(sys.argv[3])
    max_frames = int(sys.argv[4])

    logs: list[str] = []

    def log(message: str) -> None:
        logs.append(str(message))
        print(json.dumps({"event": "log", "message": str(message)}, ensure_ascii=False), flush=True)

    from extractor import LocalFrameExtractor

    extractor = LocalFrameExtractor(str(output_dir.parent), fps=fps, max_frames=max_frames, logger=log)
    dest = Path(extractor.process_video(str(video), output_dir.name))
    frames = sorted(dest.glob("*.png"))
    print(
        json.dumps(
            {
                "ok": True,
                "video_path": str(video),
                "output_dir": str(dest),
                "frame_count": len(frames),
                "logs": logs[-20:],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
