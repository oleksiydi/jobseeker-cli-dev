from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def project_root() -> Path:
    """Return repository root directory."""
    return Path(__file__).resolve().parents[2]


def find_app_config_path() -> Path:
    """
    Resolve main app config path.

    Priority:
    1) APP_CONFIG_PATH (env)
    2) conf/app.json
    """
    env_path = os.getenv("APP_CONFIG_PATH")
    if env_path:
        path = Path(env_path).expanduser()
        if path.exists():
            return path
        raise FileNotFoundError(f"APP_CONFIG_PATH points to missing file: {path}")

    path = project_root() / "conf" / "app.json"
    if path.exists():
        return path
    raise FileNotFoundError(f"Config file not found: {path}")


def load_app_config() -> dict[str, Any]:
    """Load JSON from app config path."""
    config_path = find_app_config_path()
    with config_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def find_profile_config_path(filename: str = "default.profile.json") -> Path:
    """
    Resolve profile config path.

    Priority:
    1) PROFILE_CONFIG_PATH (env)
    2) conf/profiles/<filename>
    """
    env_path = os.getenv("PROFILE_CONFIG_PATH")
    if env_path:
        path = Path(env_path).expanduser()
        if path.exists():
            return path
        raise FileNotFoundError(f"PROFILE_CONFIG_PATH points to missing file: {path}")

    path = project_root() / "conf" / "profiles" / filename
    if path.exists():
        return path
    raise FileNotFoundError(f"Profile config file not found: {path}")
