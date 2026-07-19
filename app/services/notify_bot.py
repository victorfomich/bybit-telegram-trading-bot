import asyncio
import json
from pathlib import Path

import httpx

from app.services.log_buffer import log_event

SIDE_RU = {"LONG": "лонг", "SHORT": "шорт"}


class NotifyBot:
    def __init__(self, bot_id: str, token: str, target_username: str) -> None:
        self.bot_id = bot_id
        self.token = (token or "").strip()
        self.target_username = (target_username or "fetwjdf").lstrip("@").lower()
        self._chat_id: int | None = None
        self._bot_username: str | None = None
        self._running = False
        self._last_error: str | None = None
        self._offset = 0
        from app.config import is_serverless

        if is_serverless():
            self._state_file = Path(f"/tmp/notify_state_{bot_id}.json")
        else:
            self._state_file = Path(__file__).resolve().parent.parent.parent / f"notify_state_{bot_id}.json"
        self._load_state()

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def _load_state(self) -> None:
        # совместимость со старым файлом для bot1
        candidates = [self._state_file]
        if self.bot_id == "bot1":
            candidates.append(Path(__file__).resolve().parent.parent.parent / "notify_state.json")
        for path in candidates:
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                chat_id = data.get("chat_id")
                if chat_id:
                    self._chat_id = int(chat_id)
                    break
            except Exception:
                pass

    def _save_state(self) -> None:
        self._state_file.write_text(
            json.dumps({"chat_id": self._chat_id, "username": self.target_username}, ensure_ascii=False),
            encoding="utf-8",
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.token)

    async def _request(self, method: str, payload: dict | None = None) -> dict:
        timeout = 35.0 if method == "getUpdates" else 20.0
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(self._api_url(method), json=payload or {})
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                raise RuntimeError(data.get("description", "Telegram API error"))
            return data

    async def bootstrap(self) -> None:
        if not self.is_configured:
            self._last_error = f"{self.bot_id}: токен notify-бота не задан"
            log_event("WARN", self.bot_id, self._last_error)
            return

        try:
            me = await self._request("getMe")
            self._bot_username = me["result"].get("username")
            log_event("INFO", self.bot_id, f"Notify @{self._bot_username} запущен")
        except Exception as exc:
            self._last_error = str(exc)
            log_event("ERROR", self.bot_id, f"Ошибка запуска notify: {exc}")
            return

        if not self._chat_id:
            await self._try_resolve_chat_id()

    async def _try_resolve_chat_id(self) -> None:
        try:
            data = await self._request("getChat", {"chat_id": f"@{self.target_username}"})
            chat = data["result"]
            if chat.get("username", "").lower() == self.target_username:
                self._chat_id = int(chat["id"])
                self._save_state()
                log_event("INFO", self.bot_id, f"Пользователь @{self.target_username} привязан")
        except Exception:
            pass

    async def start_polling(self) -> None:
        if not self.is_configured:
            return

        await self.bootstrap()
        self._running = True
        fail_streak = 0
        last_logged_error: str | None = None

        while self._running:
            try:
                data = await self._request(
                    "getUpdates",
                    {"offset": self._offset, "timeout": 25, "allowed_updates": ["message"]},
                )
                if fail_streak > 0:
                    log_event("INFO", self.bot_id, "Telegram polling восстановлен")
                fail_streak = 0
                last_logged_error = None
                self._last_error = None
                for update in data.get("result", []):
                    self._offset = update["update_id"] + 1
                    await self._handle_update(update)
            except Exception as exc:
                fail_streak += 1
                err = str(exc).strip() or type(exc).__name__
                self._last_error = err
                # Не спамим терминал одной и той же сетевой ошибкой
                should_log = err != last_logged_error or fail_streak in (1, 5, 20) or fail_streak % 50 == 0
                if should_log:
                    log_event(
                        "WARN",
                        self.bot_id,
                        f"Временная сеть/DNS ошибка polling ({fail_streak}x): {err}",
                    )
                    last_logged_error = err
                # backoff: 3, 6, 12... до 60 сек
                delay = min(60, 3 * (2 ** min(fail_streak - 1, 4)))
                await asyncio.sleep(delay)

    def stop(self) -> None:
        self._running = False

    async def _handle_update(self, update: dict) -> None:
        message = update.get("message")
        if not message:
            return

        user = message.get("from", {})
        username = (user.get("username") or "").lower()
        text = (message.get("text") or "").strip()
        chat_id = int(message["chat"]["id"])

        if username != self.target_username:
            return

        self._chat_id = chat_id
        self._save_state()

        if text.startswith("/start"):
            await self._send_message(
                chat_id,
                f"✅ Всё в порядке!\n\n"
                f"Бот уведомлений [{self.bot_id}] работает.\n"
                f"Вы будете получать сообщения об открытых сделках этого канала.",
            )
            log_event("INFO", self.bot_id, f"Пользователь @{username} отправил /start")

    async def _send_message(self, chat_id: int, text: str) -> None:
        await self._request(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        )

    def send_message(self, text: str) -> None:
        if not self.is_configured or not self._chat_id:
            return
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(
                    self._api_url("sendMessage"),
                    json={"chat_id": self._chat_id, "text": text, "disable_web_page_preview": True},
                )
                response.raise_for_status()
        except Exception as exc:
            self._last_error = str(exc)
            log_event("ERROR", self.bot_id, f"Не удалось отправить сообщение: {exc}")

    def send_trade_opened(self, symbol: str, margin: float, leverage: int, side: str) -> None:
        if not self.is_configured or not self._chat_id:
            return

        coin = symbol.replace("USDT", "")
        direction = SIDE_RU.get(side.upper(), side.lower())
        text = (
            f"[{self.bot_id}] Открыта сделка {coin} на сумму {margin:g} USDT "
            f"с плечом x{leverage} в {direction}"
        )

        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(
                    self._api_url("sendMessage"),
                    json={"chat_id": self._chat_id, "text": text, "disable_web_page_preview": True},
                )
                response.raise_for_status()
                data = response.json()
                if not data.get("ok"):
                    raise RuntimeError(data.get("description", "send failed"))
            log_event("INFO", self.bot_id, f"Уведомление отправлено: {coin} {direction}")
            self._last_error = None
        except Exception as exc:
            self._last_error = str(exc)
            log_event("ERROR", self.bot_id, f"Не удалось отправить уведомление: {exc}")

    def get_status(self) -> dict:
        if not self.is_configured:
            return {
                "bot_id": self.bot_id,
                "configured": False,
                "running": False,
                "username": f"@{self.target_username}",
                "user_linked": False,
                "bot_username": None,
                "ready": False,
                "message": f"Добавь токен notify для {self.bot_id} в .env",
                "last_error": self._last_error,
            }

        ready = self._running and self._chat_id is not None
        if not self._chat_id:
            msg = f"Напиши боту @{self._bot_username or '...'} команду /start"
        elif ready:
            msg = "Всё в порядке, уведомления работают"
        else:
            msg = "Бот запускается..."

        return {
            "bot_id": self.bot_id,
            "configured": True,
            "running": self._running,
            "username": f"@{self.target_username}",
            "user_linked": self._chat_id is not None,
            "bot_username": self._bot_username,
            "ready": ready,
            "message": msg,
            "last_error": self._last_error,
        }


def run_notify_bot_in_background(notify: NotifyBot) -> None:
    async def runner() -> None:
        await notify.start_polling()

    asyncio.create_task(runner())
