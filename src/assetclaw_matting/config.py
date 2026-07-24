from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _discover_aki_root(parent: Path, home: Path | None = None) -> Path:
    """Find an Aki/ComfyUI bundle without relying on its machine-specific folder name."""
    user_home = Path.home() if home is None else Path(home)
    conventional = parent / "ComfyUI-aki-v3"
    desktop_candidates = [
        user_home / "Desktop" / "ComfyUI-aki-v3",
        user_home / "OneDrive" / "Desktop" / "ComfyUI-aki-v3",
    ]
    for candidate in [conventional]:
        if (candidate / "ComfyUI").is_dir() and (candidate / "python" / "python.exe").is_file():
            return candidate

    # A bundle beside the checkout belongs to that deployment and must win
    # over an unrelated bundle on the current user's Desktop.
    search_roots = (parent, user_home / "Desktop", user_home / "OneDrive" / "Desktop")
    for search_root in search_roots:
        candidates: list[Path] = []
        try:
            candidates = [
                item
                for item in search_root.iterdir()
                if item.is_dir() and (item / "ComfyUI").is_dir() and (item / "python" / "python.exe").is_file()
            ]
        except OSError:
            continue
        if candidates:
            return sorted(candidates, key=lambda item: item.name.lower())[0]
        if search_root == parent:
            for candidate in desktop_candidates:
                if (candidate / "ComfyUI").is_dir() and (candidate / "python" / "python.exe").is_file():
                    return candidate
    return conventional


def _discover_unity_project(parent: Path) -> Path:
    """Find a sibling Unity project by structure, with a stable conventional fallback."""
    conventional = parent / "UnityProject"
    if (conventional / "Assets").is_dir() and (conventional / "ProjectSettings").is_dir():
        return conventional
    try:
        candidates = [
            item
            for item in parent.iterdir()
            if item.is_dir() and (item / "Assets").is_dir() and (item / "ProjectSettings").is_dir()
        ]
    except OSError:
        candidates = []
    return sorted(candidates, key=lambda item: item.name.lower())[0] if candidates else conventional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Only the process-global settings object loads the checkout .env.
        # Ad-hoc Settings(...) instances (tests, migrations, diagnostics) must
        # derive paths from their explicit assetclaw_root instead of silently
        # inheriting machine-specific paths from the real deployment.
        env_file=None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    assetclaw_root: Path = PROJECT_ROOT
    animation_root: Path = PROJECT_ROOT.parent / "animation_auto"
    app_env: str = "dev"

    gateway_host: str = "127.0.0.1"
    gateway_port: int = 7865
    gateway_base_url: str = "http://127.0.0.1:7865"
    public_base_url: Optional[str] = None
    feishu_callback_path: str = "/feishu/events"

    brain_provider: str = "llm_proxy"
    brain_fallback_provider: str = "local_command"
    brain_max_tool_calls: int = 5
    brain_result_summary_mode: str = "llm"
    brain_memory_enabled: bool = True
    brain_memory_recent_messages: int = 8
    brain_memory_compact_enabled: bool = True
    brain_memory_compact_notify_feishu: bool = True
    brain_memory_compact_after_messages: int = 20
    brain_memory_compact_keep_messages: int = 8
    brain_memory_compact_max_chars: int = 2400
    agent_queue_enabled: bool = True
    agent_queue_max_workers: int = 2
    agent_queue_poll_seconds: float = 0.25
    agent_queue_dispatch_grace_seconds: float = 2.0
    skill_threadpool_workers: int = 8

    llm_proxy_enabled: bool = True
    llm_proxy_base_url: str = "https://llm-proxy.lilithgames.com"
    llm_proxy_api_key: str = ""
    llm_proxy_auth_header: str = "authorization_bearer"
    llm_proxy_model: str = "claude-sonnet-4-6"
    llm_proxy_complex_model: str = "claude-sonnet-4-6"
    llm_proxy_summary_model: str = "claude-sonnet-4-6"
    llm_proxy_timeout_seconds: int = 60
    llm_proxy_openai_compatible: bool = False

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_format: str = "openai"
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_router_model: str = "deepseek-v4-flash"
    deepseek_summary_model: str = "deepseek-v4-pro"
    deepseek_thinking_type: str = "disabled"
    deepseek_reasoning_effort: str = "medium"
    deepseek_stream: bool = False
    deepseek_timeout_seconds: int = 120
    deepseek_max_retries: int = 2
    deepseek_temperature: float = 0.1

    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_verification_token: str = ""
    feishu_encrypt_key: str = ""
    feishu_default_notify_chat_id: str = ""
    feishu_admin_open_ids: str = ""
    feishu_allowed_open_ids: str = ""
    feishu_allowed_chat_ids: str = ""
    feishu_progress_reaction_enabled: bool = True
    feishu_progress_reaction_emoji_types: str = "敲键盘;keyboard;KEYBOARD;OK;THUMBSUP"
    bot_require_confirmation_for_write: bool = False
    bot_error_push_enabled: bool = True
    bot_emotional_replies_enabled: bool = True
    bot_sticker_dir: Path = PROJECT_ROOT / "miratsu_stickers"
    bot_sticker_probability: float = 0.28
    bot_sticker_cooldown_seconds: int = 180
    bot_sticker_max_bytes: int = 8_000_000
    bot_sticker_send_max_px: int = 240
    bot_sticker_extensions: str = ".png;.gif"
    user_default_location: str = "上海"
    user_food_preferences: str = ""
    feishu_ignore_events_older_than_seconds: int = 600

    # Event mode: "ws" (long connection, recommended) or "webhook" (legacy, requires public URL)
    feishu_event_mode: str = "ws"
    feishu_enable_websocket: bool = True
    feishu_enable_webhook: bool = False

    skill_api_token: str = "please_change_me"
    allow_file_list: bool = True
    allow_file_copy: bool = True
    allow_file_move: bool = True
    allow_file_mkdir: bool = True
    allow_file_delete: bool = False
    allow_file_read_content: bool = False
    allow_shell_exec: bool = False
    max_list_items: int = 100

    worker_id: str = "win3090-worker-01"
    worker_token: str = "please_change_me"
    worker_poll_interval_seconds: int = 3
    gpu_task_concurrency: int = 1
    agent_runs_on_gpu: bool = False

    data_dir: Path = PROJECT_ROOT / "data"
    storage_dir: Path = PROJECT_ROOT / "storage"
    log_dir: Path = PROJECT_ROOT / "logs"

    shared_matting_root: str = ""
    shared_matting_unc_root: str = ""
    allowed_roots: str = PROJECT_ROOT.anchor or str(PROJECT_ROOT)
    deny_path_patterns: str = (
        ".env;.ssh;Windows;$Recycle.Bin;System Volume Information"
    )

    comfyui_fake_mode: bool = False
    comfyui_aki_root: Path = PROJECT_ROOT.parent / "ComfyUI-aki-v3"
    comfyui_dir: Path = PROJECT_ROOT.parent / "ComfyUI-aki-v3" / "ComfyUI"
    comfyui_python_dir: Path = PROJECT_ROOT.parent / "ComfyUI-aki-v3" / "python"
    comfyui_python_exe: Path = PROJECT_ROOT.parent / "ComfyUI-aki-v3" / "python" / "python.exe"
    comfyui_url: str = "http://127.0.0.1:8188"
    comfyui_workflow_path: Path = PROJECT_ROOT.parent / "ComfyUI-aki-v3" / "ComfyUI" / "user" / "default" / "workflows" / "ImageClip.json"
    comfyui_timeout_seconds: int = 600
    comfyui_poll_interval_seconds: int = 2

    # Hybrid ImageClip execution.  ``local`` preserves the existing 4070Ti
    # behavior.  ``hybrid`` keeps small work local while overflowing large or
    # concurrent runs to GPU Control.  ``gpu_control`` forces all real runs to
    # the remote batch service.  The remote service performs matting only;
    # every other animation stage remains on this machine.
    matting_backend_mode: str = "local"
    gpu_control_base_url: str = "https://10.3.34.11"
    gpu_control_api_key: str = ""
    gpu_control_verify_tls: bool = True
    gpu_control_ca_bundle: str = ""
    gpu_control_allow_ca_without_key_usage: bool = False
    gpu_control_connect_timeout_seconds: int = 15
    gpu_control_request_timeout_seconds: int = 30
    gpu_control_upload_timeout_seconds: int = 86400
    gpu_control_download_timeout_seconds: int = 1800
    gpu_control_execution_timeout_seconds: int = 86400
    gpu_control_poll_interval_seconds: int = 3
    gpu_control_request_retries: int = 3
    gpu_control_poll_error_limit: int = 20
    gpu_control_large_batch_threshold: int = 64
    gpu_control_max_batch_frames: int = 5000
    gpu_control_max_inflight_batches: int = 8
    matting_pipeline_repo_url: str = "git@gitlab.lilithgame.com:rd_center/ai_art/imageclip.git"
    matting_pipeline_repo_dir: Path = PROJECT_ROOT.parent / "imageclip"
    matting_pipeline_branch: str = "main"
    matting_pipeline_workflow_name: str = "ImageClip.json"
    matting_pipeline_lora_name: str = "Koutu_Flux2klein_v2_000007250.safetensors"
    matting_pipeline_custom_node_name: str = "Cherry_lizi"
    cherry_html_runner_enabled: bool = True
    cherry_postprocess_html_path: Path = PROJECT_ROOT.parent / "imageclip" / "cherry-postprocess.html"
    cherry_browser_path: Path | None = None
    cherry_html_timeout_seconds: int = 900

    default_batch_input_dir: Path = PROJECT_ROOT / "storage" / "batch_inputs"
    default_batch_output_dir: Path = PROJECT_ROOT / "storage" / "batch_outputs"

    p4_workspace_root: str = ""
    p4_enabled: bool = False
    unity_project_dir: Path = PROJECT_ROOT.parent / "UnityProject"

    speech_engine: str = "funasr"
    speech_model: str = "iic/SenseVoiceSmall"
    speech_model_dir: Path = PROJECT_ROOT / "storage" / "models" / "asr" / "iic__SenseVoiceSmall"
    speech_fallback_model: str = "large-v3-turbo"
    speech_device: str = "cuda:0"
    speech_compute_type: str = "float16"
    speech_beam_size: int = 1
    speech_vad_filter: bool = True
    speech_use_vad: bool = False
    speech_vad_model: str = "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"
    speech_vad_model_dir: Path = PROJECT_ROOT / "storage" / "models" / "asr" / "iic__speech_fsmn_vad_zh-cn-16k-common-pytorch"
    speech_disable_update: bool = True
    tts_engine: str = "indextts"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    tts_rate: str = "+0%"
    tts_max_chars: int = 800
    bot_tts_enabled: bool = False
    voice_reply_on_audio: bool = True
    voice_reply_progress_enabled: bool = True
    indextts_repo_dir: Path = PROJECT_ROOT / "storage" / "models" / "index-tts" / "repo"
    indextts_model_dir: Path = PROJECT_ROOT / "storage" / "models" / "index-tts" / "checkpoints"
    indextts_cfg_path: Path = PROJECT_ROOT / "storage" / "models" / "index-tts" / "checkpoints" / "config.yaml"
    indextts_prompt_audio: Path = PROJECT_ROOT / "storage" / "models" / "asr" / "iic__SenseVoiceSmall" / "example" / "zh.mp3"
    indextts_emo_audio: Path | None = None
    indextts_emo_alpha: float = 0.6
    indextts_use_fp16: bool = True
    indextts_use_cuda_kernel: bool = False
    indextts_use_deepspeed: bool = False

    @model_validator(mode="before")
    @classmethod
    def derive_portable_paths(cls, values: object) -> object:
        """Derive every local default from the checkout instead of a drive letter."""
        data = dict(values) if isinstance(values, dict) else {}
        root = Path(data.get("assetclaw_root") or PROJECT_ROOT).expanduser()
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        root = root.resolve()
        parent = root.parent
        animation_root = Path(data.get("animation_root") or parent / "animation_auto").expanduser()
        comfy_root = Path(data.get("comfyui_aki_root") or _discover_aki_root(parent)).expanduser()
        comfy_dir = Path(data.get("comfyui_dir") or comfy_root / "ComfyUI").expanduser()
        comfy_python_dir = Path(data.get("comfyui_python_dir") or comfy_root / "python").expanduser()
        pipeline_root = Path(data.get("matting_pipeline_repo_dir") or parent / "imageclip").expanduser()
        defaults: dict[str, Path | str] = {
            "assetclaw_root": root,
            "animation_root": animation_root,
            "bot_sticker_dir": root / "miratsu_stickers",
            "data_dir": root / "data",
            "storage_dir": root / "storage",
            "log_dir": root / "logs",
            "allowed_roots": root.anchor or str(root),
            "unity_project_dir": _discover_unity_project(parent),
            "comfyui_aki_root": comfy_root,
            "comfyui_dir": comfy_dir,
            "comfyui_python_dir": comfy_python_dir,
            "comfyui_python_exe": comfy_python_dir / "python.exe",
            "comfyui_workflow_path": comfy_dir / "user" / "default" / "workflows" / "ImageClip.json",
            "matting_pipeline_repo_dir": pipeline_root,
            "cherry_postprocess_html_path": pipeline_root / "cherry-postprocess.html",
            "default_batch_input_dir": root / "storage" / "batch_inputs",
            "default_batch_output_dir": root / "storage" / "batch_outputs",
            "speech_model_dir": root / "storage" / "models" / "asr" / "iic__SenseVoiceSmall",
            "speech_vad_model_dir": root / "storage" / "models" / "asr" / "iic__speech_fsmn_vad_zh-cn-16k-common-pytorch",
            "indextts_repo_dir": root / "storage" / "models" / "index-tts" / "repo",
            "indextts_model_dir": root / "storage" / "models" / "index-tts" / "checkpoints",
            "indextts_cfg_path": root / "storage" / "models" / "index-tts" / "checkpoints" / "config.yaml",
            "indextts_prompt_audio": root / "storage" / "models" / "asr" / "iic__SenseVoiceSmall" / "example" / "zh.mp3",
        }
        for key, value in defaults.items():
            data.setdefault(key, value)
        return data

    @property
    def feishu_admin_open_ids_list(self) -> list[str]:
        return [x.strip() for x in self.feishu_admin_open_ids.split(";") if x.strip()]

    @property
    def feishu_allowed_open_ids_list(self) -> list[str]:
        return [x.strip() for x in self.feishu_allowed_open_ids.split(";") if x.strip()]

    @property
    def feishu_allowed_chat_ids_list(self) -> list[str]:
        return [x.strip() for x in self.feishu_allowed_chat_ids.split(";") if x.strip()]

    @property
    def data_db_path(self) -> Path:
        return self.data_dir / "assetclaw.db"

    @property
    def allowed_roots_list(self) -> list[str]:
        return [item.strip() for item in self.allowed_roots.split(";") if item.strip()]

    @property
    def deny_path_patterns_list(self) -> list[str]:
        return [item.strip() for item in self.deny_path_patterns.split(";") if item.strip()]

    @property
    def bot_sticker_extensions_list(self) -> list[str]:
        return [item.strip().lower() for item in self.bot_sticker_extensions.split(";") if item.strip()]

    def ensure_dirs(self) -> None:
        for directory in (
            self.data_dir,
            self.storage_dir,
            self.storage_dir / "batch_inputs",
            self.storage_dir / "batch_outputs",
            self.storage_dir / "batches",
            self.storage_dir / "tasks",
            self.storage_dir / "debug",
            self.storage_dir / "feishu_inbox",
            self.storage_dir / "matting_runs",
            self.storage_dir / "gpu_control_batches",
            self.log_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings(_env_file=PROJECT_ROOT / ".env")
