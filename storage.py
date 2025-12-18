from __future__ import annotations
from pathlib import Path
from typing import Any
import json


def _deep_copy_default(default: Any) -> Any:
    try:
        return json.loads(json.dumps(default))
    except Exception:
        return default


def _backup_file(path: Path, content: str) -> None:
    try:
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_text(content, encoding="utf-8")
    except Exception:
        # If backup fails we still continue with a reset
        pass


def load_json(path: Path, default: Any | None = None) -> Any:
    """
    Load JSON from path with safety:
    - If missing: return default
    - If empty or invalid: write .bak and reset to default
    """
    default = {} if default is None else default
    default_value = _deep_copy_default(default)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        return default_value

    raw_text = path.read_text(encoding="utf-8")
    text = raw_text.strip()
    if not text:
        _backup_file(path, raw_text)
        save_json(path, default_value)
        return _deep_copy_default(default_value)

    try:
        return json.loads(text)
    except Exception:
        _backup_file(path, raw_text)
        save_json(path, default_value)
        return _deep_copy_default(default_value)


def save_json(path: Path, payload: Any) -> None:
    """
    Atomic JSON write: write to temp file then replace target.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)
