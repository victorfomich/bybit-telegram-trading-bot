import asyncio
import logging

from telethon import TelegramClient, events
from telethon.tl.types import PeerChannel

from app.config import settings
from app.db import SessionLocal
from app.services.ai_manager import ai_manager
from app.services.channel_store import save_channel_message
from app.services.log_buffer import log_event
from app.services.signal_parser import parse_signal
from app.services.trade_engine import TradeEngine


logger = logging.getLogger(__name__)


class MultiChannelTelegramListener:
    """Один Telegram-аккаунт слушает несколько каналов параллельно."""

    def __init__(self, engine: TradeEngine, channels: dict[str, int]) -> None:
        self.engine = engine
        self.channels = {bid: cid for bid, cid in channels.items() if cid}
        self.channel_to_bot = {int(cid): bid for bid, cid in self.channels.items()}
        self.client = TelegramClient(
            settings.telegram_session_name,
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )

    def _resolve_bot_id(self, chat_id: int) -> str | None:
        bot_id = self.channel_to_bot.get(int(chat_id))
        if bot_id:
            return bot_id
        for cid, bid in self.channel_to_bot.items():
            if str(chat_id).endswith(str(cid).replace("-100", "")) or chat_id == cid:
                return bid
        return None

    async def start(self) -> None:
        await self.client.connect()
        if not await self.client.is_user_authorized():
            log_event("ERROR", "telegram", "Сессия не авторизована. Запусти: python scripts/auth_telegram.py")
            return

        if not self.channels:
            log_event("WARN", "telegram", "Нет настроенных каналов для прослушивания")
            return

        chats = [PeerChannel(cid) for cid in self.channels.values()]
        for bot_id, cid in self.channels.items():
            log_event("INFO", bot_id, f"Слушаю канал {cid}")

        @self.client.on(events.NewMessage(chats=chats))
        async def handler(event):
            bot_id = self._resolve_bot_id(event.chat_id)
            if not bot_id:
                log_event("WARN", "telegram", f"Неизвестный канал chat_id={event.chat_id}")
                return

            text = (event.raw_text or "").strip()
            log_event("INFO", bot_id, f"Новое сообщение из канала ({len(text)} симв.)")
            if not text:
                return

            reply_text = ""
            try:
                if event.is_reply:
                    reply = await event.get_reply_message()
                    if reply:
                        reply_text = (reply.raw_text or "").strip()
            except Exception:
                pass

            db = SessionLocal()
            try:
                saved = save_channel_message(db, text, bot_id=bot_id)
                if saved.is_signal:
                    log_event(
                        "INFO",
                        bot_id,
                        f"Сигнал распознан: {saved.symbol} {saved.side} x{saved.leverage}",
                    )
                    parsed = parse_signal(text)
                    self.engine.handle_signal(db, parsed, bot_id=bot_id)
                    return

                # Не торговый сигнал — пробуем AI follow-up (закрытие / бу / SL)
                await self._handle_followup(db, bot_id, text, reply_text)
            except ValueError as exc:
                log_event("WARN", bot_id, f"Сигнал не открыт: {exc}")
            except Exception as exc:
                log_event("ERROR", bot_id, f"Ошибка обработки: {exc}")
                logger.exception("[%s] Signal processing failed: %s", bot_id, exc)
            finally:
                db.close()

        await self.client.run_until_disconnected()

    async def _handle_followup(self, db, bot_id: str, text: str, reply_text: str) -> None:
        settings_row = self.engine.get_or_create_settings(db, bot_id)
        if not getattr(settings_row, "ai_enabled", False):
            log_event("INFO", bot_id, "Не сигнал (AI выключен)")
            return
        if not ai_manager.is_configured:
            log_event("WARN", bot_id, "Не сигнал, AI нужен, но OPENAI_API_KEY не задан")
            return

        opens = self.engine.list_open_positions(db, bot_id)
        if not opens:
            log_event("INFO", bot_id, "Не сигнал и нет открытых позиций — AI пропуск")
            return

        open_payload = [
            {
                "symbol": p.symbol,
                "side": p.side,
                "entry_price": p.entry_price,
                "tp_price": p.tp_price,
                "sl_price": p.sl_price,
                "qty": p.qty,
            }
            for p in opens
        ]

        log_event("INFO", bot_id, "AI анализирует follow-up сообщение...")
        decision = await asyncio.to_thread(
            ai_manager.interpret,
            text,
            reply_text,
            open_payload,
            bot_id,
        )
        log_event(
            "INFO",
            bot_id,
            f"AI decision: {decision.get('action')} {decision.get('symbol')} "
            f"({decision.get('confidence')}) — {decision.get('reason')}",
        )
        result = self.engine.apply_ai_action(db, bot_id, decision)
        if result.get("ok"):
            log_event("INFO", bot_id, f"AI действие выполнено: {result}")
        elif not result.get("skipped"):
            log_event("WARN", bot_id, f"AI действие не выполнено: {result.get('reason')}")

    async def fetch_last_channel_signal(self, bot_id: str) -> str | None:
        channel_id = self.channels.get(bot_id)
        if not channel_id:
            return None

        if not self.client.is_connected():
            await self.client.connect()

        messages = await self.client.get_messages(PeerChannel(channel_id), limit=50)
        for message in messages:
            text = (message.message or "").strip()
            if not text:
                continue
            try:
                parse_signal(text)
            except Exception:
                continue
            db = SessionLocal()
            try:
                save_channel_message(db, text, bot_id=bot_id)
            finally:
                db.close()
            log_event("INFO", bot_id, "Последний сигнал из канала обновлён")
            return text

        log_event("WARN", bot_id, "В последних сообщениях канала сигналов не найдено")
        return None


def run_listener_in_background(listener: MultiChannelTelegramListener) -> None:
    async def runner():
        try:
            await listener.start()
        except Exception:
            logger.exception("Telegram listener stopped")
            log_event("ERROR", "telegram", "Telegram listener stopped")

    asyncio.create_task(runner())
