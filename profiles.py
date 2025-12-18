from __future__ import annotations
import re
from pathlib import Path
from typing import List
from models import AppState
from storage import data_path, load_json, save_json

DATA_DIR = data_path("")
PROFILES_FILE = data_path("profiles.json")
LEGACY_FILE = data_path("state.json")


def _sanitize_profile_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip())
    safe = safe.strip("_") or "default"
    return safe[:80]


def _profile_path(profile_name: str) -> Path:
    safe = _sanitize_profile_name(profile_name)
    return data_path(f"state__{safe}.json")


def _save_profiles_list(profiles: List[str]) -> None:
    save_json(PROFILES_FILE, {"profiles": profiles})


def migrate_legacy_state() -> None:
    """
    One-time migration:
    - If legacy state.json exists AND no profile files exist yet
    - Import into default profile, then rename legacy file to .migrated
    """
    if not LEGACY_FILE.exists():
        return
    if any(DATA_DIR.glob("state__*.json")):
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    default_state = AppState(profile="default")
    raw = load_json(LEGACY_FILE, default_state.model_dump(mode="json"))
    try:
        migrated_state = AppState.model_validate(raw)
    except Exception:
        migrated_state = default_state

    migrated_state.profile = "default"
    save_profile("default", migrated_state)
    try:
        LEGACY_FILE.rename(LEGACY_FILE.with_suffix(LEGACY_FILE.suffix + ".migrated"))
    except Exception:
        pass


def list_profiles() -> List[str]:
    data = load_json(PROFILES_FILE, {"profiles": []})
    profiles: List[str] = [p for p in data.get("profiles", []) if isinstance(p, str)]

    # Discover any files on disk not in the list
    discovered = []
    if DATA_DIR.exists():
        for path in DATA_DIR.glob("state__*.json"):
            suffix = path.stem.replace("state__", "", 1)
            discovered.append(suffix.replace("_", " ").strip() or "default")

    combined = []
    for name in profiles + discovered:
        if name and name not in combined:
            combined.append(name)

    if not combined:
        combined = ["default"]
        _save_profiles_list(combined)

    return combined


def load_profile(profile_name: str) -> AppState:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    default_state = AppState(profile=profile_name)
    raw = load_json(_profile_path(profile_name), default_state.model_dump(mode="json"))
    try:
        state = AppState.model_validate(raw)
    except Exception:
        state = default_state
        save_profile(profile_name, state)
    state.profile = profile_name

    profiles = list_profiles()
    if profile_name not in profiles:
        profiles.append(profile_name)
        _save_profiles_list(profiles)

    return state


def save_profile(profile_name: str, state: AppState) -> None:
    state.profile = profile_name
    save_json(_profile_path(profile_name), state.model_dump(mode="json"))
    profiles = list_profiles()
    if profile_name not in profiles:
        profiles.append(profile_name)
        _save_profiles_list(profiles)


def create_profile(profile_name: str) -> AppState:
    name = profile_name.strip()
    if not name:
        raise ValueError("Profile name cannot be empty.")

    profiles = list_profiles()
    if any(p.lower() == name.lower() for p in profiles):
        raise ValueError("Profile already exists.")

    path = _profile_path(name)
    if path.exists():
        raise ValueError("A profile with that name already exists on disk.")

    state = AppState(profile=name)
    save_profile(name, state)
    profiles.append(name)
    _save_profiles_list(profiles)
    return state


def delete_profile(profile_name: str) -> None:
    path = _profile_path(profile_name)
    try:
        path.unlink()
    except FileNotFoundError:
        pass

    profiles = [p for p in list_profiles() if p != profile_name]
    if not profiles:
        profiles = ["default"]
        save_profile("default", AppState(profile="default"))
    _save_profiles_list(profiles)
