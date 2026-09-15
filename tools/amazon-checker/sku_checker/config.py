"""配置加载。"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "input_excel": "",
    "output_dir": "./output",
    "price_multiplier": 2.8,
    "price_threshold": 0.10,
    "stock_warning_threshold": 5,
    "zip_code": "91730",
    "delivery": {
        # 用户指定的允许最晚送达日期（含当天），格式 YYYY-MM-DD。
        # 后端不再自行计算工作日、周末或节假日。
        "latest_allowed_date": "",
    },
    "request_timeout": 25,
    # 受控并发数；1 保持原有 CLI 串行行为，可选 1、3、5
    "workers": 1,
    # 总尝试次数 = max_retries + 1；默认 2 即首次 + 2 次重试，共 3 次
    "max_retries": 2,
    "delay_between_requests": [1.5, 3.5],
    "max_consecutive_blocks": 8,
    "impersonate": "chrome",
    "proxy": {
        "enabled": False,
        "mode": "none",  # none | list | api
        "protocol_default": "http",
        "list_file": "",
        "list_text": "",
        "api": {
            "url": "",
            "key": "",
            "auth_user": "",
            "auth_password": "",
            "num": 50,
            "extra_params": {},
            "parser": "auto",
            "deadline_margin_seconds": 45,
            "min_pool_size": 5,
            "max_uses_per_proxy": 2,
        },
    },
    "resume": True,
    "state_file": "./output/run_state.json",
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _validate_config(cfg: dict[str, Any]) -> None:
    errors: list[str] = []
    zip_code = str(cfg.get("zip_code", "")).strip()
    if not (zip_code.isdigit() and len(zip_code) == 5):
        errors.append("zip_code 必须是 5 位美国邮编")

    try:
        multiplier = float(cfg.get("price_multiplier"))
        if multiplier <= 0:
            errors.append("price_multiplier 必须大于 0")
    except (TypeError, ValueError):
        errors.append("price_multiplier 必须是数字")

    try:
        threshold = float(cfg.get("price_threshold"))
        if not 0 <= threshold <= 1:
            errors.append("price_threshold 必须在 0 到 1 之间")
    except (TypeError, ValueError):
        errors.append("price_threshold 必须是数字")

    try:
        stock_threshold = int(cfg.get("stock_warning_threshold"))
        if stock_threshold < 1:
            errors.append("stock_warning_threshold 必须大于等于 1")
    except (TypeError, ValueError):
        errors.append("stock_warning_threshold 必须是整数")

    for key, minimum in (("request_timeout", 1), ("max_retries", 0), ("max_consecutive_blocks", 1)):
        try:
            if int(cfg.get(key)) < minimum:
                errors.append(f"{key} 必须大于等于 {minimum}")
        except (TypeError, ValueError):
            errors.append(f"{key} 必须是整数")

    try:
        workers = int(cfg.get("workers"))
        if workers not in (1, 3, 5):
            errors.append("workers 只允许 1、3、5")
    except (TypeError, ValueError):
        errors.append("workers 必须是整数，且只允许 1、3、5")

    delay = cfg.get("delay_between_requests")
    if not isinstance(delay, (list, tuple)) or len(delay) != 2:
        errors.append("delay_between_requests 必须包含最小和最大秒数")
    else:
        try:
            low, high = float(delay[0]), float(delay[1])
            if low < 0 or high < low:
                errors.append("delay_between_requests 必须满足 0 <= 最小值 <= 最大值")
        except (TypeError, ValueError):
            errors.append("delay_between_requests 必须是数字")

    delivery = cfg.get("delivery") or {}
    if not isinstance(delivery, dict):
        errors.append("delivery 必须是配置对象")
    else:
        latest_value = delivery.get("latest_allowed_date")
        latest_allowed = str(latest_value or "").strip()
        if not latest_allowed:
            errors.append("必须指定 delivery.latest_allowed_date（格式 YYYY-MM-DD）")
        else:
            try:
                from datetime import date

                parsed = date.fromisoformat(latest_allowed)
                if parsed.isoformat() != latest_allowed:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append("delivery.latest_allowed_date 必须是 YYYY-MM-DD 格式的有效日期")

    if errors:
        raise ValueError("配置错误:\n- " + "\n- ".join(errors))


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg = deepcopy(DEFAULTS)
    if path:
        p = Path(path)
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if not isinstance(data, dict):
                raise ValueError(f"配置文件格式错误: {p}")
            cfg = _deep_merge(cfg, data)
    _validate_config(cfg)
    return cfg
