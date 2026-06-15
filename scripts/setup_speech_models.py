from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


DEFAULT_ASR_MODEL = "iic/SenseVoiceSmall"
DEFAULT_VAD_MODEL = "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"
DEFAULT_TTS_MODEL = "IndexTeam/IndexTTS-2"


def main() -> int:
    parser = argparse.ArgumentParser(description="Download ASR/TTS models for the Feishu voice agent.")
    parser.add_argument("--models-root", default="storage/models", help="Local model root under this project.")
    parser.add_argument("--asr-model", default=DEFAULT_ASR_MODEL, help="ModelScope ASR model id.")
    parser.add_argument("--vad-model", default=DEFAULT_VAD_MODEL, help="ModelScope VAD model id for FunASR.")
    parser.add_argument("--tts-model", default=DEFAULT_TTS_MODEL, help="ModelScope IndexTTS model id.")
    parser.add_argument("--skip-asr", action="store_true")
    parser.add_argument("--skip-vad", action="store_true")
    parser.add_argument("--skip-tts", action="store_true")
    parser.add_argument("--install-deps", action="store_true", help="Install ModelScope/FunASR/Whisper dependencies with pip.")
    args = parser.parse_args()

    root = Path(args.models_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if args.install_deps:
        _install_deps()

    from modelscope import snapshot_download  # type: ignore

    result: dict[str, object] = {"ok": True, "models_root": str(root), "downloads": []}
    downloads: list[dict[str, str]] = []
    if not args.skip_asr:
        asr_dir = root / "asr" / _safe_name(args.asr_model)
        asr_dir.mkdir(parents=True, exist_ok=True)
        snapshot_download(args.asr_model, local_dir=str(asr_dir))
        downloads.append({"type": "asr", "model": args.asr_model, "local_dir": str(asr_dir)})
    if not args.skip_vad:
        vad_dir = root / "asr" / _safe_name(args.vad_model)
        vad_dir.mkdir(parents=True, exist_ok=True)
        snapshot_download(args.vad_model, local_dir=str(vad_dir))
        downloads.append({"type": "vad", "model": args.vad_model, "local_dir": str(vad_dir)})
    if not args.skip_tts:
        tts_dir = root / "index-tts" / "checkpoints"
        tts_dir.mkdir(parents=True, exist_ok=True)
        snapshot_download(args.tts_model, local_dir=str(tts_dir))
        downloads.append({"type": "tts", "model": args.tts_model, "local_dir": str(tts_dir)})
    result["downloads"] = downloads
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("\nSuggested .env values:")
    if not args.skip_asr:
        print(f"SPEECH_MODEL={args.asr_model}")
        print(f"SPEECH_MODEL_DIR={root / 'asr' / _safe_name(args.asr_model)}")
    if not args.skip_vad:
        print(f"SPEECH_VAD_MODEL={args.vad_model}")
        print(f"SPEECH_VAD_MODEL_DIR={root / 'asr' / _safe_name(args.vad_model)}")
    if not args.skip_tts:
        print(f"INDEXTTS_MODEL_DIR={root / 'index-tts' / 'checkpoints'}")
        print(f"INDEXTTS_CFG_PATH={root / 'index-tts' / 'checkpoints' / 'config.yaml'}")
    return 0


def _install_deps() -> None:
    packages = [
        "modelscope",
        "funasr",
        "faster-whisper",
        "openai-whisper",
    ]
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", *packages])


def _safe_name(model_id: str) -> str:
    return model_id.replace("/", "__").replace("\\", "__")


if __name__ == "__main__":
    raise SystemExit(main())
