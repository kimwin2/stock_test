from __future__ import annotations

import json
import os
from typing import Any

try:
    import boto3
except ImportError:  # Optional for local-only usage
    boto3 = None

from .models import build_default_state, build_empty_signal_payload


PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEV_DIR = os.path.join(PACKAGE_DIR, "dev")
LOCAL_SIGNAL_PATH = os.path.join(DEV_DIR, "telegram_signals.json")
LOCAL_STATE_PATH = os.path.join(DEV_DIR, "telegram_state.json")
DEFAULT_SIGNAL_KEY = "signals/telegram_faststocknews_latest.json"
DEFAULT_STATE_KEY = "signals/telegram_faststocknews_state.json"


def _ensure_dev_dir() -> None:
    os.makedirs(DEV_DIR, exist_ok=True)


def _resolve_bucket(bucket: str | None = None) -> str:
    return (bucket or os.getenv("S3_BUCKET_NAME", "")).strip()


def _resolve_signal_key(signal_key: str | None = None) -> str:
    return (signal_key or os.getenv("TELEGRAM_SIGNAL_S3_KEY", DEFAULT_SIGNAL_KEY)).strip()


def _resolve_state_key(state_key: str | None = None) -> str:
    return (state_key or os.getenv("TELEGRAM_STATE_S3_KEY", DEFAULT_STATE_KEY)).strip()


def _load_json_file(path: str) -> dict | None:
    if not path or not os.path.exists(path):
        return None

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json_file(path: str, data: dict) -> str:
    _ensure_dev_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def _load_json_s3(bucket: str, key: str) -> dict | None:
    if not bucket or not key:
        return None
    if boto3 is None:
        return None

    s3 = boto3.client("s3")
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
    except s3.exceptions.NoSuchKey:
        return None
    except Exception:
        return None

    return json.loads(response["Body"].read().decode("utf-8"))


def _save_json_s3(bucket: str, key: str, data: dict) -> str:
    if boto3 is None:
        raise RuntimeError("boto3 패키지가 없어 S3 저장을 수행할 수 없습니다.")

    s3 = boto3.client("s3")
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json; charset=utf-8",
        CacheControl="max-age=300",
    )
    return f"s3://{bucket}/{key}"


def load_telegram_signal_payload(
    channel: str = "@faststocknews",
    bucket: str | None = None,
    signal_key: str | None = None,
    local_path: str | None = None,
    prefer_local: bool | None = None,
) -> dict:
    local_target = local_path or LOCAL_SIGNAL_PATH
    bucket_name = _resolve_bucket(bucket)
    key_name = _resolve_signal_key(signal_key)

    if prefer_local is None:
        prefer_local = not bool(os.getenv("AWS_EXECUTION_ENV"))

    payload = None
    if prefer_local:
        payload = _load_json_file(local_target)
        if payload is None and bucket_name:
            payload = _load_json_s3(bucket_name, key_name)
    else:
        if bucket_name:
            payload = _load_json_s3(bucket_name, key_name)
        if payload is None:
            payload = _load_json_file(local_target)

    if payload is None:
        return build_empty_signal_payload(
            channel=channel,
            collected_at="",
            window_minutes=int(os.getenv("TG_LOOKBACK_MINUTES", "180")),
        )

    return payload


def load_telegram_signals(
    channel: str = "@faststocknews",
    bucket: str | None = None,
    signal_key: str | None = None,
    local_path: str | None = None,
    prefer_local: bool | None = None,
) -> list[dict]:
    payload = load_telegram_signal_payload(
        channel=channel,
        bucket=bucket,
        signal_key=signal_key,
        local_path=local_path,
        prefer_local=prefer_local,
    )
    return payload.get("items", [])


def save_telegram_signal_payload(
    payload: dict,
    bucket: str | None = None,
    signal_key: str | None = None,
    local_path: str | None = None,
) -> str:
    bucket_name = _resolve_bucket(bucket)
    key_name = _resolve_signal_key(signal_key)

    if bucket_name and key_name:
        return _save_json_s3(bucket_name, key_name, payload)

    return _save_json_file(local_path or LOCAL_SIGNAL_PATH, payload)


def load_telegram_state(
    channel: str = "@faststocknews",
    bucket: str | None = None,
    state_key: str | None = None,
    local_path: str | None = None,
    prefer_local: bool | None = None,
) -> dict:
    local_target = local_path or LOCAL_STATE_PATH
    bucket_name = _resolve_bucket(bucket)
    key_name = _resolve_state_key(state_key)

    if prefer_local is None:
        prefer_local = not bool(os.getenv("AWS_EXECUTION_ENV"))

    state = None
    if prefer_local:
        state = _load_json_file(local_target)
        if state is None and bucket_name:
            state = _load_json_s3(bucket_name, key_name)
    else:
        if bucket_name:
            state = _load_json_s3(bucket_name, key_name)
        if state is None:
            state = _load_json_file(local_target)

    return state or build_default_state(channel)


def save_telegram_state(
    state: dict,
    bucket: str | None = None,
    state_key: str | None = None,
    local_path: str | None = None,
) -> str:
    bucket_name = _resolve_bucket(bucket)
    key_name = _resolve_state_key(state_key)

    if bucket_name and key_name:
        return _save_json_s3(bucket_name, key_name, state)

    return _save_json_file(local_path or LOCAL_STATE_PATH, state)
