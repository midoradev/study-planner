from __future__ import annotations
import json
import shutil
from pathlib import Path
from typing import Any

from paths import get_data_dir


DATA_DIR = get_data_dir()


def _deep_copy_default(default: Any) -> Any:
    try:
        return json.loads(json.dumps(default))
    except Exception:
        return default


def data_path(filename: str | Path) -> Path:
    """
    Resolve a file path inside the external data directory.
    """
    return DATA_DIR / Path(filename)


def _backup_file(path: Path, content: str) -> None:
    try:
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_text(content, encoding="utf-8")
    except Exception:
        # If backup fails we still continue with a reset
        pass


def migrate_repo_data_once() -> None:
    """
    One-time migration from repo-local ./data to the external data directory.
    Only runs when the target directory is empty.
    """
    legacy_dir = Path(__file__).resolve().parent / "data"
    target_dir = DATA_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    has_legacy_dir = legacy_dir.exists()
    has_state_file = (legacy_dir / "state.json").exists()
    has_profile_files = list(legacy_dir.glob("state__*.json")) if legacy_dir.exists() else []

    if not (has_legacy_dir or has_state_file or has_profile_files):
        return
    if any(target_dir.iterdir()):
        return
    if not legacy_dir.is_dir():
        return

    for src in legacy_dir.iterdir():
        if not src.is_file():
            continue
        dest = target_dir / src.name
        try:
            shutil.copy2(src, dest)
        except Exception:
            continue
        try:
            src.rename(src.with_suffix(src.suffix + ".migrated"))
        except Exception:
            pass


def load_json(path: Path | str, default: Any | None = None) -> Any:
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


def save_json(path: Path | str, payload: Any) -> None:
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
