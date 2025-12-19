from __future__ import annotations
import json
import shutil
from pathlib import Path
from typing import Any


DATA_DIR = Path(__file__).resolve().parent / ".data"


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def data_path(filename: str | Path) -> Path:
    """
    Resolve a file path inside the repo-local .data directory.
    """
    ensure_data_dir()
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
    One-time migration from repo-local ./data to .data.
    Only runs when .data is empty or missing.
    """
    legacy_dir = Path(__file__).resolve().parent / "data"
    target_dir = ensure_data_dir()

    if not legacy_dir.is_dir():
        return

    legacy_files = []
    for pattern in ("state.json", "profiles.json", "state__*.json"):
        legacy_files.extend(legacy_dir.glob(pattern))

    if not legacy_files:
        return
    if any(target_dir.iterdir()):
        return

    for src in legacy_files:
        if not src.is_file():
            continue
        dest = target_dir / src.name
        if dest.exists():
            continue
        try:
            shutil.copy2(src, dest)
        except Exception:
            continue
        try:
            migrated = src.with_suffix(src.suffix + ".migrated")
            if not migrated.exists():
                src.rename(migrated)
        except Exception:
            pass


def load_json(path: Path | str) -> Any:
    """
    Load JSON from path with safety:
    - If missing: return {}
    - If empty or invalid: write .bak and reset to {}
    """
    path = Path(path)
    ensure_data_dir()
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        return {}

    try:
        raw_text = path.read_text(encoding="utf-8")
    except Exception:
        return {}

    text = raw_text.strip()
    if not text:
        _backup_file(path, raw_text)
        save_json(path, {})
        return {}

    try:
        return json.loads(text)
    except Exception:
        _backup_file(path, raw_text)
        save_json(path, {})
        return {}


def save_json(path: Path | str, payload: Any) -> None:
    """
    Atomic JSON write: write to temp file then replace target.
    """
    path = Path(path)
    ensure_data_dir()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp.replace(path)
