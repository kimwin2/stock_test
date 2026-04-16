"""
개미승리 테마 시그널 로컬 저장/로드
"""
from __future__ import annotations

import json
import os

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEV_DIR = os.path.join(PACKAGE_DIR, "dev")
LOCAL_SIGNAL_PATH = os.path.join(DEV_DIR, "antwinner_themes.json")


def _ensure_dev_dir() -> None:
    os.makedirs(DEV_DIR, exist_ok=True)


def load_antwinner_payload(local_path: str | None = None) -> dict | None:
    path = local_path or LOCAL_SIGNAL_PATH
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_antwinner_payload(payload: dict, local_path: str | None = None) -> str:
    _ensure_dev_dir()
    path = local_path or LOCAL_SIGNAL_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[INFO] 개미승리 테마 데이터를 {path}에 저장했습니다.")
    return path
