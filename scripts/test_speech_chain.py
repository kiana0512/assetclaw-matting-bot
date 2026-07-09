from __future__ import annotations

import argparse
import json
from pathlib import Path

from assetclaw_matting.skills.speech_skills import synthesize, transcribe


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test ASR and TTS for the Feishu voice agent.")
    parser.add_argument("--audio", default="", help="Optional local audio file to transcribe.")
    parser.add_argument("--text", default="你好，我是初音。语音回复链路正在测试。", help="Text to synthesize.")
    parser.add_argument("--output", default="", help="Optional TTS output path.")
    args = parser.parse_args()

    result: dict[str, object] = {"ok": True}
    if args.audio:
        result["asr"] = transcribe(args.audio)
    tts_payload = synthesize(args.text, output_path=args.output or None)
    result["tts"] = tts_payload
    result["ok"] = bool((not args.audio or dict(result["asr"]).get("ok")) and tts_payload.get("ok"))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default))
    return 0 if result["ok"] else 1


def _json_default(value: object) -> str:
    if isinstance(value, Path):
        return str(value)
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
