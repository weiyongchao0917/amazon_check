"""Configuration persistence for the order-to-Hualun workflow."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

DEFAULT_ORDER_CONFIG: dict[str, Any] = {
    "store_name": "",
    "recipient_prefix": "Claire-wyc",
    "phone": "6265225687",
    "address1": "10136 Stafford St",
    "address2": "",
    "city": "Rancho Cucamonga",
    "state": "CA",
    "zip_code": "91730",
    "input_excel": "",
    "output_dir": "",
}


def default_order_config() -> dict[str, Any]:
    return deepcopy(DEFAULT_ORDER_CONFIG)


def load_order_config(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        return default_order_config()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default_order_config()
    result = default_order_config()
    if isinstance(data, dict):
        for key in result:
            if key in data:
                result[key] = str(data[key]) if data[key] is not None else ""
    return result


def save_order_config(path: str | Path, config: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = default_order_config()
    for key in data:
        if key in config:
            data[key] = str(config[key]) if config[key] is not None else ""
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)
