from __future__ import annotations
import os
import sys
from pathlib import Path


APP_NAME = "StudyPlanner"


def get_data_dir() -> Path:
    """
    Resolve the directory used for storing local app data.
    Uses an environment override when provided, otherwise falls back to a
    per-OS user data location.
    """
    override = os.environ.get("STUDY_PLANNER_DATA_DIR")
    if override:
        base = Path(override).expanduser()
    else:
        home = Path.home()
        platform = sys.platform
        if platform == "darwin":
            base = home / "Library" / "Application Support" / APP_NAME
        elif platform.startswith("win"):
            roaming = os.environ.get("APPDATA")
            base = Path(roaming) / APP_NAME if roaming else home / "AppData" / "Roaming" / APP_NAME
        else:
            base = home / ".local" / "share" / "study-planner"

    base.mkdir(parents=True, exist_ok=True)
    return base
