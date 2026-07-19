import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from threading import Lock


@dataclass
class LogEntry:
    id: int
    time: str
    level: str
    source: str
    message: str


class BotLogBuffer:
    def __init__(self, max_size: int = 500) -> None:
        self._entries: deque[LogEntry] = deque(maxlen=max_size)
        self._next_id = 1
        self._lock = Lock()

    def add(self, level: str, source: str, message: str) -> LogEntry:
        entry = LogEntry(
            id=self._next_id,
            time=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            level=level.upper(),
            source=source,
            message=message,
        )
        with self._lock:
            self._entries.append(entry)
            self._next_id += 1
        return entry

    def get_logs(self, after_id: int = 0) -> list[dict]:
        with self._lock:
            rows = [e for e in self._entries if e.id > after_id]
        return [
            {
                "id": e.id,
                "time": e.time,
                "level": e.level,
                "source": e.source,
                "message": e.message,
            }
            for e in rows
        ]

    def all_logs(self) -> list[dict]:
        with self._lock:
            rows = list(self._entries)
        return [
            {
                "id": e.id,
                "time": e.time,
                "level": e.level,
                "source": e.source,
                "message": e.message,
            }
            for e in rows
        ]


bot_logs = BotLogBuffer()


class BufferLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            bot_logs.add(record.levelname, record.name, self.format(record))
        except Exception:
            pass


def setup_bot_logging() -> None:
    handler = BufferLogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))

    root = logging.getLogger()
    if not any(isinstance(h, BufferLogHandler) for h in root.handlers):
        root.addHandler(handler)
        root.setLevel(logging.INFO)

    for name in ("app", "app.services", "uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        if not any(isinstance(h, BufferLogHandler) for h in logger.handlers):
            logger.addHandler(handler)


def log_event(level: str, source: str, message: str) -> None:
    bot_logs.add(level, source, message)
