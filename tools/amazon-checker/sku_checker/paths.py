"""Resolve read-only bundled resources and per-user writable runtime paths."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DATA_DIRNAME = "AmazonSKUChecker"


def resource_dir(*, frozen: bool | None = None, meipass: str | None = None) -> Path:
    """Return the source/resource root, including PyInstaller's extraction root."""
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    bundle_root = meipass if meipass is not None else getattr(sys, "_MEIPASS", None)
    if is_frozen and bundle_root:
        return Path(bundle_root).resolve()
    return Path(__file__).resolve().parent.parent


def runtime_dir(*, frozen: bool | None = None, local_app_data: str | None = None) -> Path:
    """Return a writable data directory; frozen apps never write beside the EXE."""
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if is_frozen:
        base = local_app_data if local_app_data is not None else os.environ.get("LOCALAPPDATA")
        if not base:
            base = str(Path.home() / "AppData" / "Local")
        return Path(base).expanduser().resolve() / APP_DATA_DIRNAME
    return resource_dir(frozen=False) / "gui_runtime"


def default_report_dir(*, frozen: bool | None = None, documents: str | None = None) -> Path:
    """Prefer Documents for installed users and fall back to the writable data dir."""
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not is_frozen:
        return resource_dir(frozen=False) / "output"
    candidate = Path(documents).expanduser() if documents else Path.home() / "Documents"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate.resolve()
    except OSError:
        return runtime_dir(frozen=True)


PROJECT_DIR = resource_dir()
RUNTIME_DIR = runtime_dir()
CONFIG_PATH = RUNTIME_DIR / "config.yaml"
STATE_PATH = RUNTIME_DIR / "run_state.json"
LOG_PATH = RUNTIME_DIR / "gui_crash.log"
DEFAULT_REPORT_PATH = default_report_dir() / "SKU检查报告.xlsx"
