from __future__ import annotations

import asyncio
import getpass
import json
import os
import sys

try:
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    from telethon.errors.rpcerrorlist import PhoneCodeExpiredError, PhoneCodeInvalidError, SendCodeUnavailableError
    from telethon.sessions import StringSession
except ImportError:  # Optional until telegram collector is enabled
    TelegramClient = None
    SessionPasswordNeededError = Exception
    PhoneCodeExpiredError = Exception
    PhoneCodeInvalidError = Exception
    SendCodeUnavailableError = Exception
    StringSession = None


BOOTSTRAP_DIR = os.path.join(os.path.dirname(__file__), "dev")
BOOTSTRAP_STATE_PATH = os.path.join(BOOTSTRAP_DIR, "bootstrap_state.json")


def _require_telethon() -> None:
    if TelegramClient is None or StringSession is None:
        raise RuntimeError(
            "telethon 패키지가 설치되지 않았습니다. "
            "backend/requirements.txt 설치 후 다시 시도하세요."
        )


def _ensure_bootstrap_dir() -> None:
    os.makedirs(BOOTSTRAP_DIR, exist_ok=True)


def _save_bootstrap_state(data: dict) -> None:
    _ensure_bootstrap_dir()
    with open(BOOTSTRAP_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_bootstrap_state() -> dict:
    if not os.path.exists(BOOTSTRAP_STATE_PATH):
        raise FileNotFoundError(
            "bootstrap_state.json이 없습니다. 먼저 TG_LOGIN_CODE 없이 bootstrap.py를 실행해 인증 코드를 요청하세요."
        )

    with open(BOOTSTRAP_STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _clear_bootstrap_state() -> None:
    if os.path.exists(BOOTSTRAP_STATE_PATH):
        os.remove(BOOTSTRAP_STATE_PATH)


async def bootstrap_session() -> None:
    _require_telethon()

    api_id = os.getenv("TG_API_ID") or input("TG_API_ID: ").strip()
    api_hash = os.getenv("TG_API_HASH") or input("TG_API_HASH: ").strip()
    phone = os.getenv("TG_PHONE_NUMBER") or input("Telegram phone number (+82...): ").strip()
    code_from_env = os.getenv("TG_LOGIN_CODE", "").strip()
    password_from_env = os.getenv("TG_2FA_PASSWORD", "")

    client = TelegramClient(StringSession(), int(api_id), api_hash)
    await client.connect()
    try:
        if not code_from_env:
            try:
                sent = await client.send_code_request(phone)
            except SendCodeUnavailableError as e:
                raise RuntimeError(
                    "텔레그램 인증 코드를 너무 짧은 간격으로 여러 번 요청했습니다. "
                    "잠시 기다린 뒤 다시 시도하세요."
                ) from e
            if sys.stdin.isatty():
                code = input("Telegram login code: ").strip()
                if not code:
                    _save_bootstrap_state(
                        {
                            "phone": phone,
                            "phone_code_hash": sent.phone_code_hash,
                            "api_id": str(api_id),
                        }
                    )
                    raise RuntimeError(
                        "인증 코드를 입력하지 않았습니다. "
                        "backend/telegram/dev/bootstrap_state.json 상태를 저장했으니, "
                        "TG_LOGIN_CODE를 넣고 bootstrap.py를 다시 실행하세요."
                    )
                try:
                    await client.sign_in(
                        phone=phone,
                        code=code,
                        phone_code_hash=sent.phone_code_hash,
                    )
                except PhoneCodeInvalidError as e:
                    raise RuntimeError("입력한 텔레그램 인증 코드가 올바르지 않습니다. 새 코드를 다시 요청하세요.") from e
                except PhoneCodeExpiredError as e:
                    raise RuntimeError("텔레그램 인증 코드가 만료되었습니다. 새 코드를 다시 요청하세요.") from e
                except SessionPasswordNeededError:
                    password = password_from_env or getpass.getpass("Telegram 2FA password: ")
                    await client.sign_in(password=password)

                session = client.session.save()
                print("\nTG_STRING_SESSION:")
                print(session)
                return

            _save_bootstrap_state(
                {
                    "phone": phone,
                    "phone_code_hash": sent.phone_code_hash,
                    "api_id": str(api_id),
                }
            )
            print("Telegram login code sent.")
            print(f"State saved to: {BOOTSTRAP_STATE_PATH}")
            print("다음 단계에서 TG_LOGIN_CODE를 넣고 bootstrap.py를 다시 실행하세요.")
            return

        state = _load_bootstrap_state()
        code = code_from_env or input("Telegram login code: ").strip()
        if not code:
            raise RuntimeError("TG_LOGIN_CODE가 비어 있습니다. 새로 받은 코드를 넣고 다시 실행하세요.")
        try:
            await client.sign_in(
                phone=state.get("phone", phone),
                code=code,
                phone_code_hash=state.get("phone_code_hash"),
            )
        except PhoneCodeInvalidError as e:
            raise RuntimeError("입력한 텔레그램 인증 코드가 올바르지 않습니다. 새 코드를 다시 요청하세요.") from e
        except PhoneCodeExpiredError as e:
            raise RuntimeError("텔레그램 인증 코드가 만료되었습니다. 새 코드를 다시 요청하세요.") from e
        except SessionPasswordNeededError:
            password = password_from_env or getpass.getpass("Telegram 2FA password: ")
            await client.sign_in(password=password)

        session = client.session.save()
        _clear_bootstrap_state()
        print("\nTG_STRING_SESSION:")
        print(session)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(bootstrap_session())
