import json
import re

import httpx

from app.config import settings
from app.services.log_buffer import log_event


SYSTEM_PROMPT = """Ты помощник торгового бота. По сообщению из Telegram-канала реши, нужно ли изменить ОТКРЫТУЮ позицию бота.

Возможные действия:
- close: явно закрыть нашу позицию (закройте, close, фиксируем, выходим из сделки)
- breakeven: перенести стоп-лосс на цену входа (безубыток, бу, BE)
- set_sl: поставить/перенести стоп-лосс на конкретную цену
- set_tp: поставить/перенести тейк-профит на конкретную цену
- ignore: всё остальное

Верни ТОЛЬКО JSON:
{
  "action": "close|breakeven|set_sl|set_tp|ignore",
  "symbol": "LINK или LINKUSDT или null",
  "sl_price": number|null,
  "tp_price": number|null,
  "confidence": 0.0-1.0,
  "reason": "коротко почему"
}

ЖЁСТКИЕ ПРАВИЛА:
1. Действуй ТОЛЬКО по монетам из open_positions. Если в сообщении другая монета (не из open_positions) — action=ignore.
2. Анализ рынка / UPDATE / «нет смысла лонговать X» / дамп / ликвидность — это НЕ команда закрыть. action=ignore.
3. close только при ЯВНОЙ команде управления нашей позицией: «закройте», «close», «фиксируем», «выходим».
4. Если quoted/reply содержит монету нашей позиции — используй её. Иначе symbol только если монета явно в тексте и есть в open_positions.
5. "бу" / "безубыток" = breakeven только для нашей открытой монеты.
6. Если неоднозначно или confidence < 0.75 — action=ignore.
7. Не выдумывай цены: set_sl/set_tp только если цена явно указана.
8. Никогда не подставляй «единственную открытую позицию», если в тексте речь о другой монете.
"""


def _norm_symbol(symbol: str | None) -> str | None:
    if not symbol:
        return None
    s = str(symbol).upper().replace(" ", "").replace("/", "")
    if not s:
        return None
    if not s.endswith("USDT"):
        s = f"{s}USDT"
    return s


def _coin(symbol: str | None) -> str:
    s = _norm_symbol(symbol) or ""
    return s.replace("USDT", "")


class AiTradeManager:
    def __init__(self) -> None:
        self.api_key = (settings.openai_api_key or "").strip()
        self.model = settings.openai_model or "gpt-4o-mini"

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def get_status(self) -> dict:
        return {
            "configured": self.is_configured,
            "model": self.model,
            "message": "OpenAI ключ задан" if self.is_configured else "Добавь OPENAI_API_KEY в .env",
        }

    def test_connection(self) -> dict:
        """Быстрая проверка: ключ + ответ модели на тестовую команду."""
        if not self.is_configured:
            return {"ok": False, "message": "OPENAI_API_KEY не задан в .env"}

        sample_positions = [
            {
                "symbol": "LINKUSDT",
                "side": "LONG",
                "entry_price": 7.976,
                "tp_price": 8.2,
                "sl_price": 7.7,
                "qty": 10,
            }
        ]
        try:
            decision = self.interpret(
                message="Закройте LINK. Слабая монета.",
                reply_text="LINK LONG X 20\nВход: Рынок 7,976",
                open_positions=sample_positions,
                bot_id="bot2",
            )
        except Exception as exc:
            return {"ok": False, "message": f"Ошибка OpenAI: {exc}"}

        action = decision.get("action")
        symbol = decision.get("symbol")
        ok = action == "close" and symbol and "LINK" in str(symbol).upper()
        return {
            "ok": ok,
            "message": "AI работает" if ok else f"AI ответил неожиданно: {decision}",
            "decision": decision,
            "model": self.model,
        }

    def interpret(
        self,
        message: str,
        reply_text: str | None,
        open_positions: list[dict],
        bot_id: str,
    ) -> dict:
        open_positions = open_positions or []
        if not open_positions:
            return {"action": "ignore", "reason": "нет открытых позиций", "confidence": 0}

        # Быстрый стоп: в тексте другая монета, наших нет → даже без OpenAI
        guarded = self._guard_foreign_coin(message, reply_text, open_positions)
        if guarded:
            return guarded

        if not self.is_configured:
            heuristic = self._heuristic(message, reply_text, open_positions)
            if heuristic:
                heuristic["reason"] = f"heuristic (no openai): {heuristic.get('reason')}"
                return self._validate(heuristic, message, reply_text, open_positions)
            return {"action": "ignore", "reason": "OPENAI_API_KEY не задан", "confidence": 0}

        positions_brief = [
            {
                "symbol": p.get("symbol"),
                "side": p.get("side"),
                "entry_price": p.get("entry_price"),
                "tp_price": p.get("tp_price"),
                "sl_price": p.get("sl_price"),
                "qty": p.get("qty"),
            }
            for p in open_positions
        ]

        user_payload = {
            "bot_id": bot_id,
            "message": message,
            "reply_to": reply_text or "",
            "open_positions": positions_brief,
            "rule": "Действуй только по монетам из open_positions. Чужая монета = ignore.",
        }

        try:
            with httpx.Client(timeout=25.0) as client:
                response = client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "temperature": 0,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                        ],
                    },
                )
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                parsed = json.loads(content)
        except Exception as exc:
            log_event("ERROR", bot_id, f"OpenAI ошибка: {exc}")
            return {"action": "ignore", "reason": f"openai_error: {exc}", "confidence": 0}

        action = str(parsed.get("action") or "ignore").lower().strip()
        if action not in {"close", "breakeven", "set_sl", "set_tp", "ignore"}:
            action = "ignore"

        symbol = _norm_symbol(parsed.get("symbol"))
        confidence = float(parsed.get("confidence") or 0)
        result = {
            "action": action,
            "symbol": symbol,
            "sl_price": parsed.get("sl_price"),
            "tp_price": parsed.get("tp_price"),
            "confidence": confidence,
            "reason": parsed.get("reason") or "",
        }

        if action != "ignore" and confidence < 0.75:
            result["action"] = "ignore"
            result["reason"] = f"low confidence ({confidence}): {result['reason']}"

        result = self._validate(result, message, reply_text, open_positions)

        # Heuristic fallback только для явных «закройте COIN» по нашей монете
        if result["action"] == "ignore":
            heuristic = self._heuristic(message, reply_text, open_positions)
            if heuristic:
                return self._validate(heuristic, message, reply_text, open_positions)

        return result

    def _open_symbols(self, open_positions: list[dict]) -> set[str]:
        out = set()
        for p in open_positions:
            s = _norm_symbol(p.get("symbol"))
            if s:
                out.add(s)
        return out

    def _mentioned_open_symbols(self, text: str, open_positions: list[dict]) -> list[str]:
        found = []
        for p in open_positions:
            sym = _norm_symbol(p.get("symbol"))
            coin = _coin(sym)
            if not coin:
                continue
            if re.search(rf"\b{re.escape(coin)}\b", text, re.IGNORECASE):
                found.append(sym)
        return found

    def _mentioned_foreign_coins(self, text: str, open_positions: list[dict]) -> list[str]:
        """Тикеры вида BILL / MAGIC / LINK в тексте, которых нет в открытых позициях."""
        open_coins = {_coin(s) for s in self._open_symbols(open_positions)}
        # Заголовки UPDATE / LONG / SHORT + отдельные тикеры 2-10 букв
        candidates = set()
        for m in re.finditer(r"\b([A-Za-z]{2,10})\b", text):
            token = m.group(1).upper()
            if token in {
                "LONG",
                "SHORT",
                "USDT",
                "UPDATE",
                "STOP",
                "LOSS",
                "TAKE",
                "PROFIT",
                "TP",
                "SL",
                "BE",
                "VIP",
                "RSI",
                "USD",
                "USDT",
                "SPOT",
                "CHAT",
            }:
                continue
            candidates.add(token)

        # Заголовок вида "⬅️BILL UPDATE"
        for m in re.finditer(r"(?:^|\n)\W*([A-Za-z]{2,10})\s+UPDATE\b", text, re.IGNORECASE):
            candidates.add(m.group(1).upper())

        return sorted(c for c in candidates if c not in open_coins)

    def _guard_foreign_coin(
        self,
        message: str,
        reply_text: str | None,
        open_positions: list[dict],
    ) -> dict | None:
        text = f"{reply_text or ''}\n{message}"
        ours = self._mentioned_open_symbols(text, open_positions)
        foreign = self._mentioned_foreign_coins(message, open_positions)
        # В reply к нашему сигналу иностранная монета в главном тексте важнее
        if foreign and not ours:
            return {
                "action": "ignore",
                "symbol": None,
                "sl_price": None,
                "tp_price": None,
                "confidence": 1.0,
                "reason": f"сообщение про другую монету ({', '.join(foreign)}), не про открытые позиции",
            }
        return None

    def _validate(
        self,
        result: dict,
        message: str,
        reply_text: str | None,
        open_positions: list[dict],
    ) -> dict:
        if result.get("action") in (None, "ignore"):
            return result

        text = f"{reply_text or ''}\n{message}"
        open_syms = self._open_symbols(open_positions)
        ours = self._mentioned_open_symbols(text, open_positions)
        foreign = self._mentioned_foreign_coins(message, open_positions)

        if foreign and not ours:
            return {
                "action": "ignore",
                "symbol": None,
                "sl_price": None,
                "tp_price": None,
                "confidence": 1.0,
                "reason": f"отклонено: в тексте {', '.join(foreign)}, открытых нет",
            }

        symbol = _norm_symbol(result.get("symbol"))
        if not symbol:
            if len(ours) == 1:
                symbol = ours[0]
            else:
                return {
                    "action": "ignore",
                    "symbol": None,
                    "sl_price": None,
                    "tp_price": None,
                    "confidence": 0,
                    "reason": "нет symbol и нельзя безопасно угадать позицию",
                }

        if symbol not in open_syms:
            return {
                "action": "ignore",
                "symbol": symbol,
                "sl_price": None,
                "tp_price": None,
                "confidence": 1.0,
                "reason": f"нет открытой позиции {symbol}",
            }

        # Символ действия должен быть явно упомянут (или быть единственной нашей монетой в тексте)
        coin = _coin(symbol)
        if not re.search(rf"\b{re.escape(coin)}\b", text, re.IGNORECASE):
            return {
                "action": "ignore",
                "symbol": symbol,
                "sl_price": None,
                "tp_price": None,
                "confidence": 1.0,
                "reason": f"в тексте нет {coin} — не закрываем чужую/угаданную позицию",
            }

        result = {**result, "symbol": symbol}
        return result

    def _heuristic(self, message: str, reply_text: str | None, open_positions: list[dict]) -> dict | None:
        text = f"{reply_text or ''}\n{message}"
        lower = text.lower()

        # Никакого fallback «одна открытая позиция» — только явное упоминание нашей монеты
        symbols = self._mentioned_open_symbols(text, open_positions)
        if not symbols:
            return None

        symbol = symbols[0]
        if re.search(r"закр|close\b|фиксир|выходим|закрой", lower):
            return {
                "action": "close",
                "symbol": symbol,
                "sl_price": None,
                "tp_price": None,
                "confidence": 0.9,
                "reason": "heuristic close",
            }
        if re.search(r"\bбу\b|безубыт|breakeven|\bbe\b", lower):
            return {
                "action": "breakeven",
                "symbol": symbol,
                "sl_price": None,
                "tp_price": None,
                "confidence": 0.9,
                "reason": "heuristic breakeven",
            }
        return None


ai_manager = AiTradeManager()
