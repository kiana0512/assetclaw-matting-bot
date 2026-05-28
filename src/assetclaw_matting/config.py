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

    # Feishu
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_verification_token: str = ""
    feishu_default_notify_chat_id: str = ""

    # Public URL
    public_base_url: Optional[str] = None

    # Gateway
    gateway_host: str = "127.0.0.1"
    gateway_port: int = 7865
    gateway_base_url: str = "http://127.0.0.1:7865"
    worker_token: str = "please_change_me"

    # Storage
    data_dir: Path = Path("E:/assetclaw-matting-bot/data")
    storage_dir: Path = Path("E:/assetclaw-matting-bot/storage")
    log_dir: Path = Path("E:/assetclaw-matting-bot/logs")

    # ComfyUI
    comfyui_url: str = "http://127.0.0.1:8188"
    comfyui_workflow_path: Path = Path(
        "E:/assetclaw-matting-bot/workflows/matting_api.json"
    )
    comfyui_timeout_seconds: int = 600
    comfyui_poll_interval_seconds: int = 2
    comfyui_fake_mode: bool = True

    # Worker
    worker_id: str = "win3090-worker-01"
    worker_poll_interval_seconds: int = 3

    # Batch defaults
    default_batch_input_dir: Path = Path(
        "E:/assetclaw-matting-bot/storage/batch_inputs"
    )
    default_batch_output_dir: Path = Path(
        "E:/assetclaw-matting-bot/storage/batch_outputs"
    )

    # Security
    allowed_roots: str = ""
    deny_path_patterns: str = ".ssh;.env;AppData;Windows;Program Files"
    allow_file_list: bool = True
    allow_file_read_metadata: bool = True
    allow_file_read_content: bool = False
    allow_file_delete: bool = False
    allow_shell_exec: bool = False

    # OpenClaw Cloud Agent Bridge
    openclaw_enabled: bool = False
    openclaw_base_url: str = ""
    openclaw_api_key: str = ""
    openclaw_bot_id: str = ""
    openclaw_timeout_seconds: int = 60
    # local_command_first | relay_only
    openclaw_message_mode: str = "local_command_first"

    # Skill Gateway
    skill_api_enabled: bool = True
    skill_api_token: str = "please_change_me"
    skill_require_confirmation_for_dangerous: bool = True

    # Runtime
    agent_runs_on_gpu: bool = False  # must always be False
    gpu_task_concurrency: int = 1

    # Agent (legacy compat)
    agent_enabled: bool = False
    agent_llm_provider: str = "custom"
    agent_llm_base_url: str = ""
    agent_llm_api_key: str = ""
    agent_llm_model: str = ""
    agent_max_tool_calls: int = 5

    # ── Derived helpers ──────────────────────────────────────────────────────

    @property
    def tasks_dir(self) -> Path:
        return self.storage_dir / "tasks"

    @property
    def batches_dir(self) -> Path:
        return self.storage_dir / "batches"

    @property
    def debug_dir(self) -> Path:
        return self.storage_dir / "debug"

    @property
    def allowed_roots_list(self) -> list[str]:
        if not self.allowed_roots:
            return []
        return [r.strip() for r in self.allowed_roots.split(";") if r.strip()]

    @property
    def deny_path_patterns_list(self) -> list[str]:
        if not self.deny_path_patterns:
            return []
        return [p.strip() for p in self.deny_path_patterns.split(";") if p.strip()]

    def ensure_dirs(self) -> None:
        for d in (
            self.data_dir,
            self.storage_dir,
            self.tasks_dir,
            self.batches_dir,
            self.debug_dir,
            self.log_dir,
            self.default_batch_input_dir,
            self.default_batch_output_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()
