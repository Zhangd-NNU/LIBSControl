from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .models import AppConfig


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        return AppConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        allowed = AppConfig.__dataclass_fields__
        return AppConfig(**{key: value for key, value in raw.items() if key in allowed})
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return AppConfig()


def save_config(path: Path, config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
