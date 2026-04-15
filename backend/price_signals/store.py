from __future__ import annotations

import json
import os


PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEV_DIR = os.path.join(PACKAGE_DIR, "dev")
LOCAL_SIGNAL_PATH = os.path.join(DEV_DIR, "price_theme_signals.json")


def _ensure_dev_dir() -> None:
    os.makedirs(DEV_DIR, exist_ok=True)


def load_price_signal_payload(local_path: str | None = None) -> dict | None:
    path = local_path or LOCAL_SIGNAL_PATH
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_price_signal_payload(payload: dict, local_path: str | None = None) -> str:
    _ensure_dev_dir()
    path = local_path or LOCAL_SIGNAL_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path
