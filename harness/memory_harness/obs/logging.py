from __future__ import annotations

import json
import logging
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in logging.LogRecord("", 0, "", 0, "", (), None).__dict__:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_ADOPTED_LOGGERS = ("memory_fleet",)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("memory_harness")
    root.handlers[:] = [handler]
    root.setLevel(level)
    root.propagate = False
    for name in _ADOPTED_LOGGERS:
        adopted = logging.getLogger(name)
        adopted.handlers[:] = [handler]
        adopted.setLevel(level)
        adopted.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"memory_harness.{name}")
