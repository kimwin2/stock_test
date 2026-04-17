from __future__ import annotations

import json
import os

try:
    import boto3
except ImportError:  # Optional for local-only usage
    boto3 = None

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEV_DIR = os.path.join(PACKAGE_DIR, "dev")
LOCAL_SIGNAL_PATH = os.path.join(DEV_DIR, "price_theme_signals.json")
DEFAULT_SIGNAL_KEY = "signals/price_theme_signals_latest.json"


def _ensure_dev_dir() -> None:
    os.makedirs(DEV_DIR, exist_ok=True)


def _resolve_bucket(bucket: str | None = None) -> str:
    return (bucket or os.getenv("S3_BUCKET_NAME", "")).strip()


def _resolve_signal_key(signal_key: str | None = None) -> str:
    return (signal_key or os.getenv("PRICE_SIGNAL_S3_KEY", DEFAULT_SIGNAL_KEY)).strip()


def _load_json_file(path: str) -> dict | None:
    if not path or not os.path.exists(path):
        return None

    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _save_json_file(path: str, data: dict) -> str:
    _ensure_dev_dir()
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return path


def _load_json_s3(bucket: str, key: str) -> dict | None:
    if not bucket or not key or boto3 is None:
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


def load_price_signal_payload(
    bucket: str | None = None,
    signal_key: str | None = None,
    local_path: str | None = None,
    prefer_local: bool | None = None,
) -> dict | None:
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

    return payload


def save_price_signal_payload(
    payload: dict,
    bucket: str | None = None,
    signal_key: str | None = None,
    local_path: str | None = None,
) -> str:
    bucket_name = _resolve_bucket(bucket)
    key_name = _resolve_signal_key(signal_key)

    if bucket_name and key_name:
        target = _save_json_s3(bucket_name, key_name, payload)
    else:
        target = _save_json_file(local_path or LOCAL_SIGNAL_PATH, payload)

    print(f"[INFO] 가격 기반 테마 시그널 데이터를 {target}에 저장했습니다.")
    return target
