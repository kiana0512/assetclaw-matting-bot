from __future__ import annotations

from pathlib import Path
from typing import Any
import asyncio
from contextlib import contextmanager
import hashlib
import os
import sys

from assetclaw_matting.runtime_context import get_runtime_context
from assetclaw_matting.skills.security import validate_path


AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".amr", ".webm", ".mp4", ".mov"}
_FUNASR_MODEL: Any | None = None
_FUNASR_MODEL_KEY: tuple[str, str, str, bool] | None = None
_INDEXTTS_MODEL: Any | None = None
_INDEXTTS_MODEL_KEY: tuple[str, str, bool, bool, bool] | None = None


def transcribe(audio_path: str, language: str = "zh", prompt: str = "") -> dict[str, Any]:
    target = validate_path(audio_path, must_exist=True)
    if not target.is_file() or target.suffix.lower() not in AUDIO_EXTS:
        raise ValueError("audio_path must be a supported audio file")

    from assetclaw_matting.config import settings

    selected_engine = str(getattr(settings, "speech_engine", "funasr") or "funasr").strip().lower()
    text: str | None = None
    engine = ""
    if selected_engine in {"funasr", "modelscope", "sensevoice", "auto"}:
        text = _transcribe_with_funasr(target, language=language)
        engine = "funasr"
    if text is None and selected_engine in {"faster-whisper", "faster_whisper", "whisper", "auto", "funasr", "modelscope", "sensevoice"}:
        text = _transcribe_with_faster_whisper(target, language=language, prompt=prompt)
        engine = "faster-whisper"
    if text is None and selected_engine in {"openai-whisper", "openai_whisper", "whisper", "auto", "funasr", "modelscope", "sensevoice", "faster-whisper", "faster_whisper"}:
        text = _transcribe_with_openai_whisper(target, language=language, prompt=prompt)
        engine = "whisper"
    if text is None:
        return {
            "ok": False,
            "audio_path": str(target),
            "file_name": target.name,
            "text": "",
            "error": "Speech recognition is not configured. Install funasr/modelscope, faster-whisper, or openai-whisper in the bot environment.",
            "missing_dependency": True,
        }
    return {"ok": True, "audio_path": str(target), "file_name": target.name, "text": text.strip(), "engine": engine}


def synthesize(
    text: str,
    output_path: str | None = None,
    voice: str | None = None,
    engine: str | None = None,
    rate: str | None = None,
) -> dict[str, Any]:
    from assetclaw_matting.config import settings

    clean_text = " ".join(str(text or "").split())
    if not clean_text:
        raise ValueError("text is required")
    max_chars = max(20, int(settings.tts_max_chars or 800))
    if len(clean_text) > max_chars:
        clean_text = clean_text[:max_chars] + "..."
    target = _tts_output_path(output_path, clean_text, engine or settings.tts_engine)
    target.parent.mkdir(parents=True, exist_ok=True)

    selected_engine = (engine or settings.tts_engine or "edge_tts").strip().lower()
    ok_engine = ""
    engine_errors: list[str] = []
    if selected_engine in {"indextts", "index_tts", "indextts2", "index_tts2", "auto"}:
        index_target = target.with_suffix(".wav")
        try:
            if _synthesize_indextts(clean_text, index_target, prompt_audio=voice):
                target = index_target
                ok_engine = "indextts2"
        except Exception as exc:
            engine_errors.append(f"indextts2: {exc}")
    if not ok_engine and selected_engine in {"edge", "edge_tts", "auto"}:
        try:
            if _synthesize_edge_tts(clean_text, target, voice or settings.tts_voice, rate or settings.tts_rate):
                ok_engine = "edge_tts"
        except Exception as exc:
            engine_errors.append(f"edge_tts: {exc}")
    if not ok_engine and selected_engine in {"pyttsx3", "sapi", "auto", "edge", "edge_tts", "indextts", "index_tts", "indextts2", "index_tts2"}:
        wav_target = target.with_suffix(".wav")
        try:
            if _synthesize_pyttsx3(clean_text, wav_target, voice=voice):
                target = wav_target
                ok_engine = "pyttsx3"
        except Exception as exc:
            engine_errors.append(f"pyttsx3: {exc}")
    if not ok_engine:
        return {
            "ok": False,
            "text": clean_text,
            "output_path": str(target),
            "error": "TTS is not configured. Configure IndexTTS2, or install edge-tts/pyttsx3 in the bot environment.",
            "engine_errors": engine_errors,
            "missing_dependency": True,
        }
    return {"ok": True, "text": clean_text, "output_path": str(target), "file_name": target.name, "engine": ok_engine, "size": target.stat().st_size}


def send_tts(text: str, voice: str | None = None, engine: str | None = None, rate: str | None = None) -> dict[str, Any]:
    from assetclaw_matting.feishu.client import feishu_client

    payload = synthesize(text=text, voice=voice, engine=engine, rate=rate)
    if not payload.get("ok"):
        return payload
    ctx = get_runtime_context()
    chat_id = ctx.get("chat_id")
    if not chat_id:
        raise RuntimeError("speech.send_tts requires a Feishu chat context")
    target = validate_path(str(payload["output_path"]), must_exist=True)
    feishu_client.send_file_to_chat(chat_id, target, target.name)
    payload.update({"sent": True, "chat_id": chat_id})
    return payload


def _transcribe_with_funasr(path: Path, language: str) -> str | None:
    try:
        from funasr.utils.postprocess_utils import rich_transcription_postprocess  # type: ignore
    except Exception:
        return None
    from assetclaw_matting.config import settings

    try:
        model = _load_funasr_model()
        if model is None:
            return None
        result = model.generate(
            input=str(path),
            language=language or "zh",
            use_itn=True,
            batch_size_s=60,
            merge_vad=bool(getattr(settings, "speech_use_vad", False)),
            merge_length_s=15,
        )
    except Exception:
        return None
    if not result:
        return ""
    raw_text = str(result[0].get("text") if isinstance(result[0], dict) else result)
    return rich_transcription_postprocess(raw_text).strip()


def _load_funasr_model() -> Any | None:
    global _FUNASR_MODEL, _FUNASR_MODEL_KEY

    try:
        from funasr import AutoModel  # type: ignore
    except Exception:
        return None
    from assetclaw_matting.config import settings

    configured_dir = Path(settings.speech_model_dir)
    model_ref = str(configured_dir) if configured_dir.exists() else str(settings.speech_model)
    device = _normalize_funasr_device(str(settings.speech_device or "cuda:0"))
    use_vad = bool(getattr(settings, "speech_use_vad", False))
    configured_vad_dir = Path(getattr(settings, "speech_vad_model_dir", ""))
    vad_model = str(configured_vad_dir) if use_vad and configured_vad_dir.exists() else (str(settings.speech_vad_model or "") if use_vad else "")
    disable_update = bool(settings.speech_disable_update)
    key = (model_ref, device, vad_model, disable_update)
    if _FUNASR_MODEL is not None and _FUNASR_MODEL_KEY == key:
        return _FUNASR_MODEL
    kwargs: dict[str, Any] = {
        "model": model_ref,
        "trust_remote_code": True,
        "device": device,
        "disable_update": disable_update,
    }
    remote_code = Path(model_ref) / "model.py"
    if "SenseVoice" in model_ref and remote_code.exists():
        kwargs["remote_code"] = str(remote_code)
    if vad_model:
        kwargs["vad_model"] = vad_model
        kwargs["vad_kwargs"] = {"max_single_segment_time": 30000}
    _FUNASR_MODEL = AutoModel(**kwargs)
    _FUNASR_MODEL_KEY = key
    return _FUNASR_MODEL


def _normalize_funasr_device(device: str) -> str:
    normalized = (device or "").strip().lower()
    if normalized == "cuda":
        return "cuda:0"
    return normalized or "cuda:0"


def _transcribe_with_faster_whisper(path: Path, language: str, prompt: str) -> str | None:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception:
        return None
    from assetclaw_matting.config import settings

    model_name = getattr(settings, "speech_fallback_model", None) or getattr(settings, "speech_model", "large-v3-turbo")
    device = str(getattr(settings, "speech_device", "cuda") or "cuda").split(":")[0]
    compute_type = getattr(settings, "speech_compute_type", "default")
    beam_size = int(getattr(settings, "speech_beam_size", 1) or 1)
    vad_filter = bool(getattr(settings, "speech_vad_filter", True))
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
        segments, _info = model.transcribe(
            str(path),
            language=language or None,
            initial_prompt=prompt or None,
            beam_size=beam_size,
            vad_filter=vad_filter,
        )
        return "".join(segment.text for segment in segments).strip()
    except Exception:
        if device == "cuda":
            return _transcribe_with_faster_whisper_cpu(path, model_name, language=language, prompt=prompt)
        return None


def _transcribe_with_faster_whisper_cpu(path: Path, model_name: str, language: str, prompt: str) -> str | None:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception:
        return None
    try:
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(str(path), language=language or None, initial_prompt=prompt or None, beam_size=1, vad_filter=True)
        return "".join(segment.text for segment in segments).strip()
    except Exception:
        return None


def _transcribe_with_openai_whisper(path: Path, language: str, prompt: str) -> str | None:
    try:
        import whisper  # type: ignore
    except Exception:
        return None
    from assetclaw_matting.config import settings

    model_name = _openai_whisper_model_name(getattr(settings, "speech_model", "small"))
    try:
        model = whisper.load_model(model_name)
        result = model.transcribe(str(path), language=language or None, initial_prompt=prompt or None)
        return str(result.get("text") or "").strip()
    except Exception:
        return None


def _openai_whisper_model_name(model_name: str) -> str:
    normalized = (model_name or "").strip()
    if normalized in {"large-v3-turbo", "large_v3_turbo"}:
        return "turbo"
    return normalized or "turbo"


def _tts_output_path(output_path: str | None, text: str, engine: str) -> Path:
    if output_path:
        return validate_path(output_path, must_exist=False)
    from assetclaw_matting.config import settings

    suffix = ".wav" if (engine or "").lower() in {"pyttsx3", "sapi", "indextts", "index_tts", "indextts2", "index_tts2"} else ".mp3"
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    return settings.storage_dir / "tts" / f"TTS_{digest}{suffix}"


def _synthesize_indextts(text: str, target: Path, prompt_audio: str | None = None) -> bool:
    from assetclaw_matting.config import settings

    speaker_prompt = validate_path(str(prompt_audio or settings.indextts_prompt_audio), must_exist=True)
    if speaker_prompt.suffix.lower() not in AUDIO_EXTS:
        raise ValueError("IndexTTS prompt audio must be a supported audio file")
    model = _load_indextts_model()
    kwargs: dict[str, Any] = {
        "spk_audio_prompt": str(speaker_prompt),
        "text": text,
        "output_path": str(target),
        "verbose": False,
    }
    emo_audio = getattr(settings, "indextts_emo_audio", None)
    if emo_audio:
        emo_path = validate_path(str(emo_audio), must_exist=True)
        kwargs["emo_audio_prompt"] = str(emo_path)
        kwargs["emo_alpha"] = float(getattr(settings, "indextts_emo_alpha", 0.6) or 0.6)
    _infer_indextts_with_soundfile_fallback(model, kwargs)
    return target.exists() and target.stat().st_size > 0


def _infer_indextts_with_soundfile_fallback(model: Any, kwargs: dict[str, Any]) -> Any:
    try:
        import numpy as np  # type: ignore
        import soundfile as sf  # type: ignore
        import torchaudio  # type: ignore
    except Exception:
        return model.infer(**kwargs)

    original_save = torchaudio.save

    def _save(path: str, tensor: Any, sample_rate: int, *args: Any, **save_kwargs: Any) -> None:
        try:
            original_save(path, tensor, sample_rate, *args, **save_kwargs)
            return
        except Exception:
            array = tensor.detach().cpu().numpy()
            if array.ndim == 2:
                array = array.T
            if np.issubdtype(array.dtype, np.integer):
                array = array.astype("float32") / 32768.0
            sf.write(path, array, sample_rate)

    torchaudio.save = _save
    try:
        return model.infer(**kwargs)
    finally:
        torchaudio.save = original_save


def _load_indextts_model() -> Any:
    global _INDEXTTS_MODEL, _INDEXTTS_MODEL_KEY

    from assetclaw_matting.config import settings

    repo_dir = Path(settings.indextts_repo_dir)
    model_dir = validate_path(str(settings.indextts_model_dir), must_exist=True)
    cfg_path = validate_path(str(settings.indextts_cfg_path), must_exist=True)
    key = (
        str(cfg_path),
        str(model_dir),
        bool(settings.indextts_use_fp16),
        bool(settings.indextts_use_cuda_kernel),
        bool(settings.indextts_use_deepspeed),
    )
    if _INDEXTTS_MODEL is not None and _INDEXTTS_MODEL_KEY == key:
        return _INDEXTTS_MODEL
    hf_home = settings.storage_dir / "models" / "index-tts" / "hf_home"
    os.environ.setdefault("HF_HOME", str(hf_home))
    os.environ.setdefault("HF_HUB_CACHE", str(hf_home / "hub"))
    if repo_dir.exists() and str(repo_dir) not in sys.path:
        sys.path.insert(0, str(repo_dir))
    try:
        from indextts.infer_v2 import IndexTTS2  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "IndexTTS2 is not importable. Set INDEXTTS_REPO_DIR to the cloned index-tts repo "
            "or install/run the bot inside the IndexTTS environment."
        ) from exc
    with _pushd(model_dir.parent):
        _INDEXTTS_MODEL = IndexTTS2(
            cfg_path=str(cfg_path),
            model_dir=str(model_dir),
            use_fp16=bool(settings.indextts_use_fp16),
            use_cuda_kernel=bool(settings.indextts_use_cuda_kernel),
            use_deepspeed=bool(settings.indextts_use_deepspeed),
        )
    _INDEXTTS_MODEL_KEY = key
    return _INDEXTTS_MODEL


@contextmanager
def _pushd(path: Path):
    old_cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old_cwd)


def _synthesize_edge_tts(text: str, target: Path, voice: str, rate: str) -> bool:
    try:
        import edge_tts  # type: ignore
    except Exception:
        return False

    async def _run() -> None:
        communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
        await communicate.save(str(target))

    asyncio.run(_run())
    return target.exists() and target.stat().st_size > 0


def _synthesize_pyttsx3(text: str, target: Path, voice: str | None = None) -> bool:
    try:
        import pyttsx3  # type: ignore
    except Exception:
        return False
    engine = pyttsx3.init()
    if voice:
        for item in engine.getProperty("voices") or []:
            if voice.lower() in str(getattr(item, "id", "")).lower() or voice.lower() in str(getattr(item, "name", "")).lower():
                engine.setProperty("voice", item.id)
                break
    engine.save_to_file(text, str(target))
    engine.runAndWait()
    return target.exists() and target.stat().st_size > 0
