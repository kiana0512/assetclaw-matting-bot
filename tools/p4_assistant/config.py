from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PACKAGE_DIR / "workspaces.yaml"
LOCAL_CONFIG_PATH = PACKAGE_DIR / "workspaces.local.yaml"
EXAMPLE_CONFIG_PATH = PACKAGE_DIR / "workspaces.example.yaml"

DEFAULT_MANAGED_PATHS = (
    "Assets/Art/UI/SpritesAnim/Emoji/...",
    "Assets/Art/UI/SpritesAnim/CharacterAnim/...",
    "Assets/Art/UI/Animation/Emoji/...",
    "Assets/Art/Character/Animation/...",
    "Assets/Res/UI/Animator/EmojiOverride/...",
    "Assets/Res/Character/Animation/Override/...",
    "Assets/Modules/UepUtility/AnimTextureImporter/Editor/Manifests/...",
)

DEFAULT_FORBIDDEN_PATHS = (
    "ProjectSettings/...",
    "Assets/ProjectSettings/...",
    "Assets/AddressableAssetsData/...",
    "Assets/Plugins/...",
    "Assets/Editor/...",
    "Packages/...",
    "Library/...",
    "Temp/...",
    "UserSettings/...",
    "Logs/...",
)

EXAMPLE_CONFIG: dict = {
    "workspaces": {
        "spark_client_ui": {
            "p4port": "spark-p4.lilithgames.com:1666",
            "p4user": "keizhang",
            "p4client": "keizhang_L-20260528ZLGJA_8024",
            "root": "D:/Spark/Client",
            "mode": "shelve_only",
            "managed_paths": list(DEFAULT_MANAGED_PATHS),
            "forbidden_paths": list(DEFAULT_FORBIDDEN_PATHS),
        }
    }
}


def default_config_hint() -> str:
    return (
        f"No real workspace config was found at {DEFAULT_CONFIG_PATH} or {LOCAL_CONFIG_PATH}. "
        f"Using the built-in example shape. Copy {EXAMPLE_CONFIG_PATH.name} to "
        "workspaces.local.yaml and edit workspace/client/root values when ready."
    )
