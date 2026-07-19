from datetime import datetime

from sqlalchemy.orm import Session

from app.models import ChannelMessage
from app.services.signal_parser import parse_signal


def save_channel_message(db: Session, raw_text: str, bot_id: str = "bot1") -> ChannelMessage:
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("Empty message")

    last = (
        db.query(ChannelMessage)
        .filter(ChannelMessage.bot_id == bot_id)
        .order_by(ChannelMessage.received_at.desc())
        .first()
    )
    if last and last.raw_text == text:
        return last

    record = ChannelMessage(
        bot_id=bot_id,
        raw_text=text,
        is_signal=False,
        received_at=datetime.utcnow(),
    )

    try:
        parsed = parse_signal(text)
        record.is_signal = True
        record.symbol = parsed.symbol
        record.side = parsed.side
        record.leverage = parsed.leverage
        record.entry_kind = parsed.entry_kind
        record.entry_market = parsed.entry_market
        record.entry_limit = parsed.entry_limit
        record.tp1 = parsed.tp1
        record.tp2 = parsed.tp2
        record.tp3 = parsed.tp3
        record.sl = parsed.sl
        record.parse_error = None
    except Exception as exc:
        record.parse_error = str(exc)

    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_last_channel_message(db: Session, bot_id: str = "bot1") -> ChannelMessage | None:
    return (
        db.query(ChannelMessage)
        .filter(ChannelMessage.bot_id == bot_id)
        .order_by(ChannelMessage.received_at.desc())
        .first()
    )


def get_last_signal_message(db: Session, bot_id: str = "bot1") -> ChannelMessage | None:
    return (
        db.query(ChannelMessage)
        .filter(ChannelMessage.bot_id == bot_id, ChannelMessage.is_signal.is_(True))
        .order_by(ChannelMessage.received_at.desc())
        .first()
    )


def channel_message_to_dict(msg: ChannelMessage | None) -> dict | None:
    if not msg:
        return None
    return {
        "id": msg.id,
        "bot_id": msg.bot_id,
        "raw_text": msg.raw_text,
        "is_signal": msg.is_signal,
        "symbol": msg.symbol,
        "side": msg.side,
        "leverage": msg.leverage,
        "entry_kind": msg.entry_kind,
        "entry_market": msg.entry_market,
        "entry_limit": msg.entry_limit,
        "tp1": msg.tp1,
        "tp2": msg.tp2,
        "tp3": msg.tp3,
        "sl": msg.sl,
        "parse_error": msg.parse_error,
        "received_at": msg.received_at.isoformat() if msg.received_at else None,
    }
