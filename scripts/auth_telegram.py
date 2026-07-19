"""One-time Telegram user account authorization (QR or phone)."""

import asyncio
import base64
import io
import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import qrcode
from qrcode.constants import ERROR_CORRECT_H
from telethon import TelegramClient, errors
from telethon.tl.types.auth import (
    SentCodeTypeApp,
    SentCodeTypeCall,
    SentCodeTypeEmailCode,
    SentCodeTypeSms,
)

from app.config import settings


def describe_delivery(sent) -> str:
    code_type = sent.type
    if isinstance(code_type, SentCodeTypeApp):
        return (
            "Код отправлен В ПРИЛОЖЕНИЕ Telegram (не SMS).\n"
            "Открой Telegram на телефоне -> чат 'Telegram' / 'Login code'."
        )
    if isinstance(code_type, SentCodeTypeSms):
        return "Код отправлен по SMS на ваш номер."
    if isinstance(code_type, SentCodeTypeCall):
        return "Код будет продиктован звонком."
    if isinstance(code_type, SentCodeTypeEmailCode):
        return "Код отправлен на email, привязанный к аккаунту."
    return f"Код отправлен способом: {type(code_type).__name__}"


def build_qr_image(url: str):
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_H,
        box_size=16,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white")


def show_qr(url: str) -> tuple[Path, Path]:
    png_path = ROOT / "telegram_login_qr.png"
    html_path = ROOT / "telegram_login_qr.html"

    img = build_qr_image(url)
    img.save(png_path)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    b64 = base64.b64encode(buffer.getvalue()).decode("ascii")

    html_path.write_text(
        f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Telegram QR Login</title>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, sans-serif;
      background: #111;
      color: #fff;
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 100vh;
      margin: 0;
      padding: 24px;
    }}
    .card {{
      background: #1f1f1f;
      border-radius: 16px;
      padding: 28px;
      max-width: 520px;
      text-align: center;
      box-shadow: 0 10px 40px rgba(0,0,0,.35);
    }}
    img {{
      width: 360px;
      height: 360px;
      background: #fff;
      padding: 16px;
      border-radius: 12px;
    }}
    ol {{
      text-align: left;
      line-height: 1.6;
      margin-top: 20px;
    }}
    .warn {{
      color: #ffb84d;
      margin-top: 16px;
      font-size: 14px;
    }}
    code {{
      word-break: break-all;
      font-size: 12px;
      color: #9fd3ff;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Вход в Telegram</h1>
    <img src="data:image/png;base64,{b64}" alt="Telegram QR" />
    <ol>
      <li>На телефоне открой <b>Telegram</b></li>
      <li>Перейди: <b>Настройки → Устройства → Подключить устройство</b></li>
      <li>Нажми <b>Сканировать QR-код</b> (внутри Telegram, не камера iPhone)</li>
      <li>Наведи на этот QR на экране Mac</li>
    </ol>
    <p class="warn">QR действует ~30 секунд. Если не успел — в терминале появится новый.</p>
    <p><code>{url}</code></p>
  </div>
</body>
</html>
""",
        encoding="utf-8",
    )

    print("\nQR открывается в браузере:", html_path)
    print("PNG (запасной):", png_path)
    print("\nВАЖНО: сканировать нужно ТОЛЬКО через Telegram:")
    print("Настройки -> Устройства -> Подключить устройство -> Сканировать QR")
    print("Не используй обычную камеру телефона.\n")

    webbrowser.open(html_path.as_uri())
    try:
        subprocess.run(["open", str(html_path)], check=False)
    except Exception:
        pass

    return png_path, html_path


async def login_with_qr(client: TelegramClient):
    while True:
        qr_login = await client.qr_login()
        show_qr(qr_login.url)
        print("Ожидаю сканирование QR... (действует ~30 сек)")

        try:
            await qr_login.wait()
            return
        except errors.SessionPasswordNeededError:
            password = input("Введите пароль 2FA Telegram: ").strip()
            await client.sign_in(password=password)
            return
        except asyncio.TimeoutError:
            print("QR истек. Генерирую новый...")
        except Exception as exc:
            print(f"Ошибка QR-входа: {exc}. Пробую снова...")


async def login_with_phone(client: TelegramClient):
    phone = input("Введите номер телефона (например +77471227901): ").strip()
    sent = await client.send_code_request(phone)
    print("\n" + describe_delivery(sent) + "\n")

    while True:
        code = input("Введите код (или r для повторной отправки): ").strip()
        if code.lower() in {"r", "resend", "повтор"}:
            sent = await client.send_code_request(phone)
            print("\n" + describe_delivery(sent) + "\n")
            continue

        try:
            await client.sign_in(phone=phone, code=code)
            return
        except errors.PhoneCodeInvalidError:
            print("Неверный код. Попробуйте еще раз или введите r для нового кода.")
        except errors.PhoneCodeExpiredError:
            print("Код истек. Отправляю новый...")
            sent = await client.send_code_request(phone)
            print("\n" + describe_delivery(sent) + "\n")
        except errors.SessionPasswordNeededError:
            password = input("Введите пароль 2FA Telegram: ").strip()
            await client.sign_in(password=password)
            return


async def main() -> None:
    print("=" * 60)
    print("Авторизация Telegram для торгового бота")
    print("=" * 60)

    client = TelegramClient(
        settings.telegram_session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"Уже авторизован: {me.first_name} (@{me.username})")
        await client.disconnect()
        return

    mode = input("Выберите способ входа: [1] QR-код (рекомендуется) / [2] Телефон: ").strip()
    if mode == "2":
        await login_with_phone(client)
    else:
        await login_with_qr(client)

    me = await client.get_me()
    print(f"\nГотово! Авторизован: {me.first_name} (@{me.username})")
    print("Теперь перезапусти сервер: uvicorn app.main:app --host 0.0.0.0 --port 8000")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
