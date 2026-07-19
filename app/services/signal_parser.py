import re

from app.schemas import ParsedSignal


def _to_float(value: str) -> float:
    return float(value.strip().replace(",", "."))


def parse_signal(text: str) -> ParsedSignal:
    """Парсит сигналы обоих каналов:
    1) BCH LONG x26 / SL: / TP:
    2) DASH SHORT X 15 / Stop-loss: / Take-Profit:
    """
    normalized = text.replace("\u00a0", " ")

    symbol_match = re.search(
        r"^([A-Z0-9]+)\s+.*?\b(LONG|SHORT)\b\s*[xX]\s*(\d+)",
        normalized,
        re.MULTILINE,
    )
    if not symbol_match:
        raise ValueError("Cannot parse symbol/side/leverage")
    symbol, side, leverage_raw = symbol_match.groups()

    market_match = re.search(r"Рынок\s*([0-9.,]+)", normalized, re.IGNORECASE)
    limit_match = re.search(r"Лимит\w*\s*([0-9.,]+)", normalized, re.IGNORECASE)
    if market_match and limit_match:
        # Bot1: рынок + лимит → делим маржу пополам
        entry_kind = "BOTH"
    elif limit_match:
        entry_kind = "LIMIT"
    else:
        entry_kind = "MARKET"

    tp_matches = re.findall(r"\d\)\s*([0-9.,]+)", normalized)
    if len(tp_matches) < 3:
        raise ValueError("Cannot parse three TP values")

    sl_match = re.search(
        r"(?:SL|Stop[\s\-]*loss)\s*:\s*([0-9.,]+)",
        normalized,
        re.IGNORECASE,
    )
    if not sl_match:
        raise ValueError("Cannot parse SL")

    return ParsedSignal(
        raw_text=text,
        symbol=f"{symbol}USDT",
        side=side.upper(),
        leverage=int(leverage_raw),
        entry_kind=entry_kind,
        entry_market=_to_float(market_match.group(1)) if market_match else None,
        entry_limit=_to_float(limit_match.group(1)) if limit_match else None,
        tp1=_to_float(tp_matches[0]),
        tp2=_to_float(tp_matches[1]),
        tp3=_to_float(tp_matches[2]),
        sl=_to_float(sl_match.group(1)),
    )
