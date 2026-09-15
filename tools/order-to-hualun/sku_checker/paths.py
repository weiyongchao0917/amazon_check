"""Resolve read-only bundled resources and per-user writable runtime paths."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

APP_DATA_DIRNAME = "OrderToHualun"


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
ORDER_TEMPLATE_PATH = PROJECT_DIR / "templates" / "花轮模板.xlsx"
ORDER_CONFIG_PATH = RUNTIME_DIR / "order_config.json"


def _is_ascii_path(path: Path) -> bool:
    try:
        str(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _ascii_cache_dir() -> Path | None:
    """Choose a writable ASCII-only directory for Windows libcurl assets."""
    candidates = [
        Path(tempfile.gettempdir()) / "OrderToHualun",
        Path("C:/Temp/OrderToHualun"),
    ]
    for candidate in candidates:
        if not _is_ascii_path(candidate):
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue
    return None


def configure_curl_ca_bundle(
    *, cert_path: str | Path | None = None, cache_dir: str | Path | None = None
) -> Path | None:
    """Make curl_cffi use a CA bundle path that libcurl can open on Windows.

    PyInstaller can place certifi's bundle under a Unicode application path.
    Some Windows libcurl builds report error 77 for that path even when the
    file exists, so mirror it to an ASCII-only temporary path and expose it
    through CURL_CA_BUNDLE before creating the curl_cffi session.
    """
    try:
        if cert_path is None:
            import certifi

            cert_path = certifi.where()
        source = Path(cert_path)
    except Exception:
        return None
    if not source.is_file():
        return None
    if _is_ascii_path(source):
        os.environ["CURL_CA_BUNDLE"] = str(source)
        return source

    target_dir = Path(cache_dir) if cache_dir is not None else _ascii_cache_dir()
    if target_dir is None or not _is_ascii_path(target_dir):
        return None
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "cacert.pem"
        if not target.is_file() or target.stat().st_size != source.stat().st_size:
            temp_target = target.with_suffix(".tmp")
            shutil.copyfile(source, temp_target)
            temp_target.replace(target)
        os.environ["CURL_CA_BUNDLE"] = str(target)
        return target
    except OSError:
        return None
