"""User-selected local study and inbox folders; never stores documents in settings."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def folder_path(value: str | Path, *, create: bool = False) -> Path:
    raw = str(value)
    if not raw or len(raw) > 1024 or "\x00" in raw:
        raise ValueError("Choose an absolute folder path")
    folder = Path(raw).expanduser()
    if not folder.is_absolute():
        raise ValueError("Choose an absolute folder path")
    folder = folder.resolve()
    if folder.exists() and not folder.is_dir():
        raise ValueError("Selected path is not a folder")
    if create:
        folder.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(dir=folder, prefix=".fd-check-", delete=True):
                pass
        except OSError as exc:
            raise ValueError("Selected folder is not writable") from exc
    return folder


def load_paths(settings_file: Path, default_root: Path) -> tuple[Path, Path | None]:
    if not settings_file.is_file():
        return default_root.resolve(), None
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        if not isinstance(settings, dict) or settings.get("version") != 1:
            raise ValueError("Unsupported storage settings")
        root = folder_path(settings["data_root"])
        if not root.is_dir():
            raise ValueError("Configured study folder is unavailable")
        inbox_raw = settings.get("inbox_root", "")
        inbox = folder_path(inbox_raw) if inbox_raw else None
        if inbox is not None and not inbox.is_dir():
            raise ValueError("Configured inbox folder is unavailable")
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Storage settings need correction: {settings_file} ({exc})") from exc
    return root, inbox


def save_paths(settings_file: Path, data_root: Path, inbox_root: Path | None) -> None:
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "data_root": str(data_root),
               "inbox_root": str(inbox_root) if inbox_root else ""}
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=settings_file.parent,
                                         prefix=".fd-settings-", suffix=".json", delete=False) as temp:
            json.dump(payload, temp, ensure_ascii=False)
            temp.flush()
            os.fsync(temp.fileno())
            temp_name = temp.name
        os.replace(temp_name, settings_file)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
