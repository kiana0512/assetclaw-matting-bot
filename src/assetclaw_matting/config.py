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

    llm_proxy_enabled: bool = True
    llm_proxy_base_url: str = "https://llm-proxy.lilithgames.com"
    llm_proxy_api_key: str = ""
    llm_proxy_auth_header: str = "authorization_bearer"
    llm_proxy_model: str = "claude-sonnet-4-6"
    llm_proxy_complex_model: str = "claude-sonnet-4-6"
    llm_proxy_summary_model: str = "claude-sonnet-4-6"
    llm_proxy_timeout_seconds: int = 60
    llm_proxy_openai_compatible: bool = False

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

    allowed_roots: str = "D:;E:;F:"
    deny_path_patterns: str = (
        ".env;.ssh;AppData;Windows;Program Files;ProgramData;"
        "$Recycle.Bin;System Volume Information"
    )

    comfyui_fake_mode: bool = True
    comfyui_dir: Path = Path("E:/assetclaw-matting-bot/ComfyUI")
    comfyui_url: str = "http://127.0.0.1:8188"
    comfyui_workflow_path: Path = Path("E:/assetclaw-matting-bot/workflows/matting_api.json")
    comfyui_timeout_seconds: int = 600
    comfyui_poll_interval_seconds: int = 2

    default_batch_input_dir: Path = Path("E:/assetclaw-matting-bot/storage/batch_inputs")
    default_batch_output_dir: Path = Path("E:/assetclaw-matting-bot/storage/batch_outputs")

    p4_workspace_root: str = ""
    p4_enabled: bool = False

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

    def ensure_dirs(self) -> None:
        for directory in (
            self.data_dir,
            self.storage_dir,
            self.storage_dir / "batch_inputs",
            self.storage_dir / "batch_outputs",
            self.storage_dir / "batches",
            self.storage_dir / "tasks",
            self.storage_dir / "debug",
            self.log_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()
