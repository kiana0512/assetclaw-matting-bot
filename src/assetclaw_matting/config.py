from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    assetclaw_root: str = "E:\\assetclaw-matting-bot"
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
    bot_require_confirmation_for_write: bool = False
    bot_error_push_enabled: bool = True
    bot_emotional_replies_enabled: bool = True
    bot_sticker_dir: Path = Path("E:/assetclaw-matting-bot/miratsu_stickers")
    bot_sticker_probability: float = 1.0
    bot_sticker_max_bytes: int = 8_000_000
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

    data_dir: Path = Path("E:/assetclaw-matting-bot/data")
    storage_dir: Path = Path("E:/assetclaw-matting-bot/storage")
    log_dir: Path = Path("E:/assetclaw-matting-bot/logs")

    shared_matting_root: str = r"Z:\公共机共享\抠图"
    shared_matting_unc_root: str = r"\\audioshare.lilith.com\AIart\公共机共享\抠图"
    allowed_roots: str = r"D:;E:;F:;Z:;C:\Users\lilithgames\Downloads\ComfyUI-aki-v3;\\audioshare.lilith.com\AIart\公共机共享\抠图"
    deny_path_patterns: str = (
        ".env;.ssh;AppData;Windows;Program Files;ProgramData;"
        "$Recycle.Bin;System Volume Information"
    )

    comfyui_fake_mode: bool = False
    comfyui_aki_root: Path = Path("C:/Users/lilithgames/Downloads/ComfyUI-aki-v3")
    comfyui_dir: Path = Path("C:/Users/lilithgames/Downloads/ComfyUI-aki-v3/ComfyUI")
    comfyui_python_dir: Path = Path("C:/Users/lilithgames/Downloads/ComfyUI-aki-v3/python")
    comfyui_url: str = "http://127.0.0.1:8188"
    comfyui_workflow_path: Path = Path("C:/Users/lilithgames/Downloads/ComfyUI-aki-v3/ComfyUI/user/default/workflows/软边缘测试-动画批量.json")
    comfyui_timeout_seconds: int = 600
    comfyui_poll_interval_seconds: int = 2

    default_batch_input_dir: Path = Path("E:/assetclaw-matting-bot/storage/batch_inputs")
    default_batch_output_dir: Path = Path("E:/assetclaw-matting-bot/storage/batch_outputs")

    p4_workspace_root: str = ""
    p4_enabled: bool = False

    speech_engine: str = "funasr"
    speech_model: str = "iic/SenseVoiceSmall"
    speech_model_dir: Path = Path("E:/assetclaw-matting-bot/storage/models/asr/iic__SenseVoiceSmall")
    speech_fallback_model: str = "large-v3-turbo"
    speech_device: str = "cuda:0"
    speech_compute_type: str = "float16"
    speech_beam_size: int = 1
    speech_vad_filter: bool = True
    speech_use_vad: bool = False
    speech_vad_model: str = "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"
    speech_vad_model_dir: Path = Path("E:/assetclaw-matting-bot/storage/models/asr/iic__speech_fsmn_vad_zh-cn-16k-common-pytorch")
    speech_disable_update: bool = True
    tts_engine: str = "indextts"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    tts_rate: str = "+0%"
    tts_max_chars: int = 800
    voice_reply_on_audio: bool = True
    voice_reply_progress_enabled: bool = True
    indextts_repo_dir: Path = Path("E:/assetclaw-matting-bot/storage/models/index-tts/repo")
    indextts_model_dir: Path = Path("E:/assetclaw-matting-bot/storage/models/index-tts/checkpoints")
    indextts_cfg_path: Path = Path("E:/assetclaw-matting-bot/storage/models/index-tts/checkpoints/config.yaml")
    indextts_prompt_audio: Path = Path("E:/assetclaw-matting-bot/storage/models/asr/iic__SenseVoiceSmall/example/zh.mp3")
    indextts_emo_audio: Path | None = None
    indextts_emo_alpha: float = 0.6
    indextts_use_fp16: bool = True
    indextts_use_cuda_kernel: bool = False
    indextts_use_deepspeed: bool = False

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
            self.log_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()
